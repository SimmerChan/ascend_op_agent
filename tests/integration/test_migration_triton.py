"""U11: Path-B Triton 迁移图集成测试。

覆盖:

- ``make_triton_frontend_node``:mock LLM 返回严格 JSON 后,op_info 与
  arch_mapping 被正确写入 state(migration_strategy=triton_to_ascendc,
  ref_code_type=triton,arch_mapping.source_type=triton)
- ``build_migration_graph(source_type="triton")``:端到端跑通
  triton_frontend → design (HITL) → codegen → review_fix → compile (占位) →
  precision (占位) → done
- 与 cuda 路径的差异:migration_strategy/ref_code_type/arch_mapping.source_type
- ``use_real_skill_bundles=True``:注入 5-skill 链文本(含 triton-* skill 名)

mock agent_factory 避开真实 LLM 调用。
"""

from __future__ import annotations

from typing import Any

import pytest

from ascend_op_agent.orchestrator import (
    STATUS_DONE,
    STATUS_WAITING_CONFIRM,
    CheckpointStore,
    Node,
    build_migration_graph,
    make_triton_frontend_node,
)


# ---- 共享 mock(与 test_migration_cuda.py 同形) ----


class FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = FakeMemory()
        self.captured_overrides: list[Any] = []
        self.captured_inputs: list[str] = []
        self._responses = list(responses) if responses else ["fallback"]

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override: Any = None,
        *,
        task_type: Any = None,
    ) -> str:
        self.captured_inputs.append(user_input)
        self.captured_overrides.append(skills_layer_override)
        self._conversation_history.append({"role": "user", "content": user_input})
        response = self._responses.pop(0) if self._responses else "fallback"
        self._conversation_history.append({"role": "assistant", "content": response})
        return response


def _factory_from(responses: list[str]):
    def _factory() -> _FakeAgent:
        agent_responses = queue[:] if (queue := list(responses)) else ["fallback"]
        return _FakeAgent(responses=agent_responses)
    return _factory


_TRITON_JSON_RESPONSE = """分析 Triton 算子,基于 triton-* 5-skill 链入口(triton-task-extractor):

```json
{
  "op_info": {
    "name": "softmax",
    "description": "Triton softmax kernel",
    "op_type": "reduction",
    "input_shapes": [[128, 128]],
    "input_dtypes": ["float16"],
    "output_shapes": [[128, 128]],
    "output_dtypes": ["float16"],
    "migration_strategy": "triton_to_ascendc",
    "ref_code_type": "triton"
  },
  "arch_mapping": {
    "source_type": "triton",
    "mappings": {
      "tl.program_id": "GetProgramId",
      "tl.load": "GlobalTensor.Load",
      "tl.store": "GlobalTensor.Store",
      "tl.reduce_max": "ReduceMax"
    }
  }
}
```
"""


_NO_JSON_RESPONSE = "I cannot parse this"


# ---- make_triton_frontend_node 单元 ----


def test_triton_frontend_node_parses_json_into_state() -> None:
    """mock LLM 返回 JSON → op_info + design_doc.arch_mapping 写入 state。"""
    def factory() -> _FakeAgent:
        return _FakeAgent(responses=[_TRITON_JSON_RESPONSE])

    node = make_triton_frontend_node(agent_factory=factory)

    state = {
        "messages": [{"role": "user", "content": "@triton.jit def softmax(...): ..."}],
        "memory_pools": {},
        "pending_confirmation": None,
    }
    update = node.func(state)

    assert update["op_info"]["migration_strategy"] == "triton_to_ascendc"
    assert update["op_info"]["ref_code_type"] == "triton"
    assert update["design_doc"]["arch_mapping"]["source_type"] == "triton"
    assert "tl.program_id" in update["design_doc"]["arch_mapping"]["mappings"]


def test_triton_frontend_node_parse_error_non_fatal() -> None:
    """LLM 不输出 JSON 时,parse_error 写入 last_phase_result,不阻塞。"""
    def factory() -> _FakeAgent:
        return _FakeAgent(responses=[_NO_JSON_RESPONSE])

    node = make_triton_frontend_node(agent_factory=factory)
    state = {
        "messages": [{"role": "user", "content": "triton code"}],
        "memory_pools": {},
        "pending_confirmation": None,
    }
    update = node.func(state)

    assert "op_info" not in update
    assert update["last_phase_result"]["parse_error"] is not None


def test_triton_frontend_node_skips_llm_on_pending_resume() -> None:
    """pending_confirmation 非 None 时(resume 路径),跳过 LLM。"""
    call_count = {"n": 0}

    def factory() -> _FakeAgent:
        def _spy_run(*a, **kw):
            call_count["n"] += 1
            return _TRITON_JSON_RESPONSE

        agent = _FakeAgent()
        agent.run_conversation = _spy_run  # type: ignore[method-assign]
        return agent

    node = make_triton_frontend_node(agent_factory=factory)
    state = {
        "messages": [{"role": "user", "content": "triton code"}],
        "memory_pools": {},
        "pending_confirmation": {"phase": "triton_frontend", "options": ["approve"]},
    }
    update = node.func(state)

    assert call_count["n"] == 0
    assert update["pending_confirmation"] is None
    assert update["last_phase_result"]["skipped_llm"] is True


# ---- build_migration_graph triton 端到端 ----


def test_triton_migration_invoke_interrupts_at_design(tmp_path) -> None:
    """invoke 跑 triton_frontend → design 中断(design 是 HITL)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from([_TRITON_JSON_RESPONSE, "design doc"])

    runner = build_migration_graph(
        store=store,
        source_type="triton",
        agent_factory=factory,
    )
    state = runner.invoke("@triton.jit def softmax(...): ...", thread_id="t1")

    assert state["op_info"]["migration_strategy"] == "triton_to_ascendc"
    assert state["design_doc"]["arch_mapping"]["source_type"] == "triton"
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    assert state["current_phase"] == "design"
    pending = state["pending_confirmation"]
    assert pending["phase"] == "design"
    # design HITL payload 携带 op_info + arch_mapping
    assert pending["op_info"]["migration_strategy"] == "triton_to_ascendc"
    assert pending["arch_mapping"]["source_type"] == "triton"
    # design 中断不算完成
    assert "triton_frontend" in state["phase_history"]
    assert "design" not in state["phase_history"]


def test_triton_migration_resume_completes_all_phases(tmp_path) -> None:
    """design 批准后 → ... → delivery_mode HITL → resume(sample) → done。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(
        [_TRITON_JSON_RESPONSE, "design doc", "kernel.cpp", "LGTM"]
    )

    runner = build_migration_graph(
        store=store,
        source_type="triton",
        agent_factory=factory,
    )
    runner.invoke("@triton.jit def softmax(...): ...", thread_id="t1")
    runner.resume("t1", payload={"approved": True})
    state = runner.resume("t1", payload={"mode": "sample"})

    expected = [
        "entry",
        "triton_frontend",
        "design",
        "codegen",
        "review_fix",
        "compile",
        "precision",
        "delivery_mode",
        "framework_adapt",
        "done",
    ]
    assert state["phase_history"] == expected
    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE
    assert state["op_info"]["migration_strategy"] == "triton_to_ascendc"


# ---- cuda vs triton 路径区分 ----


def test_triton_and_cuda_paths_produce_different_strategy(tmp_path) -> None:
    """同图调用不同 source_type,op_info.migration_strategy / ref_code_type 区分。"""
    triton_store = CheckpointStore(tmp_path / "triton.db")
    triton_factory = _factory_from([_TRITON_JSON_RESPONSE, "design doc"])
    triton_runner = build_migration_graph(
        store=triton_store,
        source_type="triton",
        agent_factory=triton_factory,
    )
    triton_state = triton_runner.invoke("triton code", thread_id="t1")
    assert triton_state["op_info"]["migration_strategy"] == "triton_to_ascendc"
    assert triton_state["op_info"]["ref_code_type"] == "triton"

    cuda_json = """```json
{"op_info": {"name": "add", "migration_strategy": "cuda_to_ascendc", "ref_code_type": "cuda"},
 "arch_mapping": {"source_type": "cuda", "mappings": {"blockIdx.x": "GetBlockIdx<0>()"}}}
```"""
    cuda_store = CheckpointStore(tmp_path / "cuda.db")
    cuda_factory = _factory_from([cuda_json, "design doc"])
    cuda_runner = build_migration_graph(
        store=cuda_store,
        source_type="cuda",
        agent_factory=cuda_factory,
    )
    cuda_state = cuda_runner.invoke("cuda code", thread_id="t2")
    assert cuda_state["op_info"]["migration_strategy"] == "cuda_to_ascendc"
    assert cuda_state["op_info"]["ref_code_type"] == "cuda"


# ---- 真实 skill bundle 注入 ----


def test_use_real_skill_bundles_injects_triton_5_skill_text(tmp_path) -> None:
    """use_real_skill_bundles=True 时,frontend 节点 skills_layer_override 含
    triton 5-skill 链(skill 名 triton-task-extractor 等)。
    """
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent(responses=[_TRITON_JSON_RESPONSE])
        agents.append(a)
        return a

    runner = build_migration_graph(
        store=store,
        source_type="triton",
        agent_factory=factory,
        use_real_skill_bundles=True,
    )
    runner.invoke("triton code", thread_id="t1")

    frontend_overrides: list[Any] = []
    for a in agents:
        frontend_overrides.extend(a.captured_overrides)
    non_empty = [o for o in frontend_overrides if o]
    assert len(non_empty) >= 1
    # 文本应至少含一个 triton-* skill 名 + Available Skills 标记
    text_blob = "\n".join(non_empty)
    assert "Available Skills" in text_blob
    has_triton_skill = (
        "triton-task-extractor" in text_blob
        or "triton-op-designer" in text_blob
        or "triton-op-coding" in text_blob
    )
    assert has_triton_skill, f"triton skill name missing in {text_blob[:500]}"


def test_real_triton_skill_text_contains_all_5_skill_names(tmp_path) -> None:
    """build_skill_bundle(triton_frontend) 加载 5 个 skill,渲染文本含全部名称。"""
    from ascend_op_agent.orchestrator.cannbot_loader import (
        build_skill_bundle,
        render_skill_bundle_text,
    )

    skills = build_skill_bundle(phase="triton_frontend", graph="migration")
    assert len(skills) == 5
    names = {s.name for s in skills}
    expected = {
        "triton-task-extractor",
        "triton-op-designer",
        "triton-op-coding",
        "triton-op-verifier",
        "triton-latency-optimizer",
    }
    assert names == expected

    text = render_skill_bundle_text(skills, phase="triton_frontend")
    for name in expected:
        assert name in text


# ---- 自定义 compile factory(U13 入口) ----


def test_custom_compile_node_factory_works_for_triton(tmp_path) -> None:
    """triton 路径同样支持 compile_node_factory 注入。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(
        [_TRITON_JSON_RESPONSE, "design doc", "kernel.cpp", "LGTM"]
    )

    def custom_compile_factory() -> Node:
        def _real_compile(state: dict) -> dict:
            return {
                "compile_result": {
                    "success": True,
                    "command": "bash build.sh",
                    "stdout": "ok",
                    "stderr": "",
                    "return_code": 0,
                    "custom": True,
                },
            }
        return Node(name="compile", func=_real_compile)

    runner = build_migration_graph(
        store=store,
        source_type="triton",
        agent_factory=factory,
        compile_node_factory=custom_compile_factory,
    )
    runner.invoke("triton code", thread_id="t1")
    runner.resume("t1", payload={"approved": True})
    runner.resume("t1", payload={"mode": "sample"})

    state = store.load("t1")
    assert state["compile_result"]["custom"] is True
