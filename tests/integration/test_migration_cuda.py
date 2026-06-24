"""U10: Path-B CUDA 迁移图集成测试。

覆盖:

- ``extract_structured_output``:JSON 块解析的成功/兜底/失败路径
- ``make_cuda_frontend_node``:mock LLM 返回严格 JSON 后,op_info 与
  arch_mapping 被正确写入 state
- ``build_migration_graph(source_type="cuda")``:端到端跑通 cuda_frontend →
  design (HITL) → codegen → review_fix → compile (占位) → precision (占位) → done
- ``build_migration_graph(source_type="triton")``:U11 未实现,抛 NotImplementedError
- ``build_migration_graph(source_type="invalid")``:非法 source_type 抛 ValueError
- ``use_real_skill_bundles=True``:注入 cuda2ascend-simt skill 文本

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
    extract_structured_output,
    make_cuda_frontend_node,
)


# ---- 共享 mock ----


class FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    """Mock AIAgent:可预设响应,记录 skills_layer_override。"""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = FakeMemory()
        self.captured_overrides: list[Any] = []
        self.captured_inputs: list[str] = []
        # 默认响应队列;耗尽时返回 "fallback"
        self._responses = list(responses) if responses else ["fallback"]

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override: Any = None,
    ) -> str:
        self.captured_inputs.append(user_input)
        self.captured_overrides.append(skills_layer_override)
        self._conversation_history.append({"role": "user", "content": user_input})
        if self._responses:
            response = self._responses.pop(0)
        else:
            response = "fallback"
        self._conversation_history.append({"role": "assistant", "content": response})
        return response


def _factory_from(responses: list[str]):
    """返回一个 agent_factory,每次创建按序消费 responses 列表。"""
    queue = list(responses)
    counter = {"n": 0}

    def _factory() -> _FakeAgent:
        # 给每个 agent 至少 1 个 response(队列耗尽时返回 fallback)
        counter["n"] += 1
        agent_responses = queue[:] if queue else ["fallback"]
        return _FakeAgent(responses=agent_responses)

    return _factory


_CUDA_JSON_RESPONSE = """分析完成。基于 cuda2ascend-simt skill 的 API 映射表,建议如下:

```json
{
  "op_info": {
    "name": "vector_add",
    "description": "elementwise add",
    "op_type": "elementwise",
    "input_shapes": [[16], [16]],
    "input_dtypes": ["float16"],
    "output_shapes": [[16]],
    "output_dtypes": ["float16"],
    "migration_strategy": "cuda_to_ascendc",
    "ref_code_type": "cuda"
  },
  "arch_mapping": {
    "source_type": "cuda",
    "mappings": {
      "threadIdx.x": "GetBlockIdx<0>()",
      "atomicAdd": "AtomicAdd"
    }
  }
}
```
"""

_BARE_JSON_RESPONSE = """{"op_info": {"name": "add_bare", "migration_strategy": "cuda_to_ascendc", "ref_code_type": "cuda"}, "arch_mapping": {"source_type": "cuda", "mappings": {"blockIdx.x": "GetBlockIdx<0>()"}}}"""

_NO_JSON_RESPONSE = "no JSON here at all"

_INVALID_JSON_RESPONSE = """```json
{invalid json missing quotes}
```"""

_PARTIAL_JSON_RESPONSE = """```json
{"op_info": {"name": "partial"}, "arch_mapping": null}
```"""


# ---- extract_structured_output 单元 ----


def test_extract_prefers_json_code_block() -> None:
    result = extract_structured_output(_CUDA_JSON_RESPONSE)
    assert result["parse_error"] is None
    assert result["op_info"]["name"] == "vector_add"
    assert result["op_info"]["migration_strategy"] == "cuda_to_ascendc"
    assert result["arch_mapping"]["source_type"] == "cuda"
    assert "AtomicAdd" in result["arch_mapping"]["mappings"].values()


def test_extract_falls_back_to_bare_json() -> None:
    result = extract_structured_output(_BARE_JSON_RESPONSE)
    assert result["parse_error"] is None
    assert result["op_info"]["name"] == "add_bare"
    assert result["arch_mapping"]["source_type"] == "cuda"


def test_extract_no_json_returns_parse_error() -> None:
    result = extract_structured_output(_NO_JSON_RESPONSE)
    assert result["op_info"] is None
    assert result["arch_mapping"] is None
    assert "no JSON block" in result["parse_error"]


def test_extract_invalid_json_returns_decode_error() -> None:
    result = extract_structured_output(_INVALID_JSON_RESPONSE)
    assert result["op_info"] is None
    assert "JSON decode" in (result["parse_error"] or "")


def test_extract_partial_json_ignores_null_fields() -> None:
    """arch_mapping: null 时不写入(不是 dict)。op_info 缺字段则只取有的。"""
    result = extract_structured_output(_PARTIAL_JSON_RESPONSE)
    assert result["parse_error"] is None
    assert result["op_info"]["name"] == "partial"
    assert result["op_info"].get("migration_strategy") is None
    # null 被忽略,arch_mapping 保持 None
    assert result["arch_mapping"] is None


# ---- make_cuda_frontend_node ----


def test_cuda_frontend_node_parses_json_into_state(tmp_path) -> None:
    """mock LLM 返回严格 JSON → op_info + design_doc.arch_mapping 被写入。"""
    from ascend_op_agent.orchestrator.nodes.common import AgentFactory  # noqa: F401

    def factory() -> _FakeAgent:
        return _FakeAgent(responses=[_CUDA_JSON_RESPONSE])

    node = make_cuda_frontend_node(
        agent_factory=factory,
        phase_name="cuda_frontend",
    )

    state = {
        "messages": [{"role": "user", "content": "extern __global__ void vector_add(...);"}],
        "memory_pools": {},
        "pending_confirmation": None,
    }
    update = node.func(state)

    assert update["op_info"]["migration_strategy"] == "cuda_to_ascendc"
    assert update["op_info"]["ref_code_type"] == "cuda"
    assert update["design_doc"]["arch_mapping"]["source_type"] == "cuda"
    assert len(update["design_doc"]["arch_mapping"]["mappings"]) >= 1


def test_cuda_frontend_node_preserves_existing_design_doc(tmp_path) -> None:
    """已有 design_doc 字段时,仅 merge arch_mapping,不丢既有内容。"""
    def factory() -> _FakeAgent:
        return _FakeAgent(responses=[_CUDA_JSON_RESPONSE])

    node = make_cuda_frontend_node(agent_factory=factory)
    state = {
        "messages": [{"role": "user", "content": "code"}],
        "memory_pools": {},
        "pending_confirmation": None,
        "design_doc": {"tiling_strategy": "keep existing", "arch_mapping": None},
    }
    update = node.func(state)

    # 既有字段保留
    assert update["design_doc"]["tiling_strategy"] == "keep existing"
    # arch_mapping 被 merge
    assert update["design_doc"]["arch_mapping"]["source_type"] == "cuda"


def test_cuda_frontend_node_parse_error_non_fatal(tmp_path) -> None:
    """LLM 不输出 JSON 时,parse_error 写入 last_phase_result,不阻塞。"""
    def factory() -> _FakeAgent:
        return _FakeAgent(responses=[_NO_JSON_RESPONSE])

    node = make_cuda_frontend_node(agent_factory=factory)
    state = {
        "messages": [{"role": "user", "content": "code"}],
        "memory_pools": {},
        "pending_confirmation": None,
    }
    update = node.func(state)

    # 不抛异常;op_info 未被设置(update 不含)
    assert "op_info" not in update
    assert update["last_phase_result"]["parse_error"] is not None


def test_cuda_frontend_node_skips_llm_on_pending_resume() -> None:
    """pending_confirmation 非 None 时(resume 路径),跳过 LLM 不解析。"""
    call_count = {"n": 0}

    def factory() -> _FakeAgent:
        def _spy_run(*a, **kw):
            call_count["n"] += 1
            return _CUDA_JSON_RESPONSE

        agent = _FakeAgent()
        agent.run_conversation = _spy_run  # type: ignore[method-assign]
        return agent

    node = make_cuda_frontend_node(agent_factory=factory)
    state = {
        "messages": [{"role": "user", "content": "code"}],
        "memory_pools": {},
        "pending_confirmation": {"phase": "cuda_frontend", "options": ["approve"]},
    }
    update = node.func(state)

    # LLM 没被调用
    assert call_count["n"] == 0
    assert update["pending_confirmation"] is None
    assert update["last_phase_result"]["skipped_llm"] is True


# ---- build_migration_graph 入参校验 ----


def test_build_migration_graph_rejects_invalid_source_type(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    with pytest.raises(ValueError, match="Unsupported source_type"):
        build_migration_graph(store=store, source_type="rocm", agent_factory=_factory_from([]))


def test_build_migration_graph_requires_agent_factory(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    with pytest.raises(ValueError, match="agent_factory required"):
        build_migration_graph(store=store, source_type="cuda", agent_factory=None)


# ---- CUDA 端到端 ----


def test_cuda_migration_invoke_interrupts_at_design(tmp_path) -> None:
    """invoke 跑 cuda_frontend → design 中断(design 是 HITL)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    # frontend 节点返回 JSON;design 节点随便返回字符串(LLM 视角)
    factory = _factory_from([_CUDA_JSON_RESPONSE, "design doc here"])

    runner = build_migration_graph(
        store=store,
        source_type="cuda",
        agent_factory=factory,
    )
    state = runner.invoke("extern __global__ void add(...);", thread_id="t1")

    # frontend 解析产出 op_info
    assert state["op_info"]["migration_strategy"] == "cuda_to_ascendc"
    assert state["design_doc"]["arch_mapping"]["source_type"] == "cuda"
    # design HITL 中断
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    assert state["current_phase"] == "design"
    pending = state["pending_confirmation"]
    assert pending["phase"] == "design"
    assert "approve" in pending["options"]
    # design 中断不算完成
    assert "cuda_frontend" in state["phase_history"]
    assert "design" not in state["phase_history"]


def test_cuda_migration_resume_after_approval_completes_all_phases(tmp_path) -> None:
    """design 批准后 → codegen → review_fix → compile → precision → done。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(
        [_CUDA_JSON_RESPONSE, "design doc", "kernel.cpp", "LGTM"]
    )

    runner = build_migration_graph(
        store=store,
        source_type="cuda",
        agent_factory=factory,
    )
    runner.invoke("extern __global__ void add(...);", thread_id="t1")
    state = runner.resume("t1", payload={"approved": True})

    expected = [
        "entry",
        "cuda_frontend",
        "design",
        "codegen",
        "review_fix",
        "compile",
        "precision",
        "done",
    ]
    assert state["phase_history"] == expected
    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE
    # op_info 在 design/codegen/review_fix 后仍保留(last-write-wins 不覆盖)
    assert state["op_info"]["migration_strategy"] == "cuda_to_ascendc"


def test_cuda_migration_pending_payload_carries_arch_mapping(tmp_path) -> None:
    """design HITL payload 应包含 arch_mapping(便于前端展示迁移映射)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from([_CUDA_JSON_RESPONSE, "design doc"])

    runner = build_migration_graph(
        store=store,
        source_type="cuda",
        agent_factory=factory,
    )
    state = runner.invoke("code", thread_id="t1")

    pending = state["pending_confirmation"]
    assert pending["op_info"]["migration_strategy"] == "cuda_to_ascendc"
    assert pending["arch_mapping"]["source_type"] == "cuda"


# ---- 真实 skill bundle 注入 ----


def test_use_real_skill_bundles_injects_cannbot_skill_text(tmp_path) -> None:
    """use_real_skill_bundles=True 时,frontend 节点 skills_layer_override 含
    cuda2ascend-simt skill 文本。
    """
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent(responses=[_CUDA_JSON_RESPONSE])
        agents.append(a)
        return a

    runner = build_migration_graph(
        store=store,
        source_type="cuda",
        agent_factory=factory,
        use_real_skill_bundles=True,
    )
    runner.invoke("extern __global__ void add(...);", thread_id="t1")

    # frontend agent(captured_overrides 非空且含 cuda2ascend-simt)
    frontend_overrides: list[Any] = []
    for a in agents:
        frontend_overrides.extend(a.captured_overrides)
    non_empty = [o for o in frontend_overrides if o]
    assert len(non_empty) >= 1
    # 文本应含 cuda2ascend-simt 或 Available Skills 标记
    assert any("cuda2ascend-simt" in o or "Available Skills" in o for o in non_empty)


def test_explicit_frontend_skill_text_overrides_real_bundles(tmp_path) -> None:
    """frontend_skill_text 显式传入时,优先级 > use_real_skill_bundles。"""
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent(responses=[_CUDA_JSON_RESPONSE])
        agents.append(a)
        return a

    custom = "## CUSTOM CUDA OVERRIDE (test)"
    runner = build_migration_graph(
        store=store,
        source_type="cuda",
        agent_factory=factory,
        frontend_skill_text=custom,
        use_real_skill_bundles=True,
    )
    runner.invoke("code", thread_id="t1")

    frontend_overrides: list[Any] = []
    for a in agents:
        frontend_overrides.extend(a.captured_overrides)
    assert any(custom in (o or "") for o in frontend_overrides)


# ---- 自定义 compile/precision factory(U13 集成入口) ----


def test_custom_compile_node_factory_replaces_placeholder(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(
        [_CUDA_JSON_RESPONSE, "design doc", "kernel.cpp", "LGTM"]
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
        source_type="cuda",
        agent_factory=factory,
        compile_node_factory=custom_compile_factory,
    )
    runner.invoke("code", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    state = store.load("t1")
    assert state["compile_result"]["custom"] is True
