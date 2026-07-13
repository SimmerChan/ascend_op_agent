"""U12: 交付模式选择节点 + 框架适配集成测试。

覆盖:

- ``recommend_delivery_mode`` 启发式:torch_npu / pybind / sample 三分支
- ``make_delivery_mode_node``:非 LLM 节点
  - 首次跑产 __interrupt__(3 选项 + 推荐项 + op_info/code_result 摘要)
  - resume 后写 state["delivery_mode"],清 pending_confirmation
- ``make_framework_adapt_node``:路由跳过逻辑
  - sample / pybind 模式 skipped=True
  - torch_npu 模式 skipped=False,跑 LLM
- 集成 new_dev / migration 图:precision 之后、done 之前

mock agent_factory 避开真实 LLM 调用。
"""

from __future__ import annotations

from typing import Any

import pytest

from ascend_op_agent.orchestrator import (
    STATUS_DONE,
    STATUS_WAITING_CONFIRM,
    CheckpointStore,
    build_migration_graph,
    build_new_dev_graph,
    make_delivery_mode_node,
    make_framework_adapt_node,
    recommend_delivery_mode,
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


# ---- 启发式 recommend_delivery_mode ----


def test_recommend_returns_sample_when_no_code() -> None:
    """无 code_result → 默认 sample。"""
    assert recommend_delivery_mode({}) == "sample"
    assert recommend_delivery_mode({"code_result": None}) == "sample"


def test_recommend_returns_torch_npu_when_import_present() -> None:
    """code_result.files 含 'import torch_npu' → torch_npu。"""
    state = {
        "code_result": {
            "files": [
                {"path": "main.cpp", "content": "import torch_npu\ntorch.npu.foo()"},
            ]
        }
    }
    assert recommend_delivery_mode(state) == "torch_npu"


def test_recommend_returns_pybind_when_torch_extension_present() -> None:
    """含 PYBIND11_MODULE → pybind。"""
    state = {
        "code_result": {
            "files": [
                {"path": "binding.cpp", "content": "PYBIND11_MODULE(ext, m) {}"},
            ]
        }
    }
    assert recommend_delivery_mode(state) == "pybind"


def test_recommend_returns_pybind_when_torch_h_present() -> None:
    """含 #include <torch/extension.h> → pybind。"""
    state = {
        "code_result": {
            "files": [
                {"path": "binding.cpp", "content": "#include <torch/extension.h>"},
            ]
        }
    }
    assert recommend_delivery_mode(state) == "pybind"


def test_recommend_returns_sample_for_pure_kernel() -> None:
    """纯 AscendC kernel(无可识别 import) → sample。"""
    state = {
        "code_result": {
            "files": [
                {"path": "kernel.cpp", "content": 'extern "C" __global__ void add() {}'},
            ]
        }
    }
    assert recommend_delivery_mode(state) == "sample"


def test_recommend_prefers_torch_npu_over_pybind() -> None:
    """同时含 torch_npu 和 PYBIND11_MODULE → 优先 torch_npu。"""
    state = {
        "code_result": {
            "files": [
                {
                    "path": "main.cpp",
                    "content": "import torch_npu\nPYBIND11_MODULE(ext, m) {}",
                }
            ]
        }
    }
    assert recommend_delivery_mode(state) == "torch_npu"


# ---- make_delivery_mode_node ----


def test_delivery_mode_node_first_run_emits_interrupt() -> None:
    """首次跑(无 pending):产 __interrupt__,含 3 选项 + 推荐项。"""
    node = make_delivery_mode_node(phase="delivery_mode")
    state = {
        "code_result": {"files": [{"path": "m.cpp", "content": "import torch_npu"}]},
        "op_info": {"name": "softmax"},
        "pending_confirmation": None,
    }
    update = node.func(state)

    assert "__interrupt__" in update
    payload = update["__interrupt__"]
    assert payload["phase"] == "delivery_mode"
    assert payload["options"] == ["sample", "torch_npu", "pybind"]
    assert payload["recommended"] == "torch_npu"
    assert payload["op_info"]["name"] == "softmax"
    assert payload["code_result_summary"]["file_count"] == 1


def test_delivery_mode_node_resume_writes_mode() -> None:
    """resume 路径(pending 非 None):写 state['delivery_mode'],清 pending。"""
    node = make_delivery_mode_node()
    state = {
        "pending_confirmation": {"phase": "delivery_mode", "mode": "torch_npu"},
    }
    update = node.func(state)

    assert update["delivery_mode"] == "torch_npu"
    assert update["pending_confirmation"] is None


def test_delivery_mode_node_resume_invalid_mode_falls_back_to_sample() -> None:
    """非法 mode → 兜底 sample(不阻塞)。"""
    node = make_delivery_mode_node()
    state = {
        "pending_confirmation": {"phase": "delivery_mode", "mode": "invalid_xyz"},
    }
    update = node.func(state)
    assert update["delivery_mode"] == "sample"


# ---- make_framework_adapt_node ----


def test_framework_adapt_skips_when_sample() -> None:
    """sample 模式:跳过,返回 skipped=True。"""
    node = make_framework_adapt_node()
    state = {"delivery_mode": "sample"}
    update = node.func(state)
    assert update["framework_adapt_result"]["skipped"] is True
    assert "delivery_mode=sample" in update["framework_adapt_result"]["reason"]


def test_framework_adapt_skips_when_pybind() -> None:
    """pybind 模式同样跳过。"""
    node = make_framework_adapt_node()
    state = {"delivery_mode": "pybind"}
    update = node.func(state)
    assert update["framework_adapt_result"]["skipped"] is True


def test_framework_adapt_runs_llm_when_torch_npu() -> None:
    """torch_npu 模式 + 有 agent_factory:跑 LLM,标记 skipped=False。"""
    seen_overrides: list[Any] = []

    def factory() -> _FakeAgent:
        agent = _FakeAgent(responses=["torch_npu ext code"])
        orig_run = agent.run_conversation

        def _spy_run(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
        ):
            seen_overrides.append(skills_layer_override)
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        agent.run_conversation = _spy_run  # type: ignore[method-assign]
        return agent

    node = make_framework_adapt_node(
        agent_factory=factory,
        skill_bundle_text="## Skill: ascendc-direct-invoke-template",
    )
    state = {
        "delivery_mode": "torch_npu",
        "messages": [{"role": "user", "content": "kernel"}],
        "memory_pools": {},
        "pending_confirmation": None,
    }
    update = node.func(state)

    assert update["framework_adapt_result"]["skipped"] is False
    assert update["framework_adapt_result"]["mode"] == "torch_npu"
    # LLM 被调用
    assert len(seen_overrides) == 1
    assert "ascendc-direct-invoke-template" in (seen_overrides[0] or "")


def test_framework_adapt_placeholder_when_no_factory_but_torch_npu() -> None:
    """torch_npu 模式 + 无 agent_factory:返回 placeholder(不抛错)。"""
    node = make_framework_adapt_node(agent_factory=None)
    state = {"delivery_mode": "torch_npu"}
    update = node.func(state)
    assert update["framework_adapt_result"]["skipped"] is False
    assert "placeholder" in update["framework_adapt_result"]["note"]


def test_framework_adapt_skips_llm_when_sample() -> None:
    """sample 模式:LLM 永不被调用。"""
    call_count = {"n": 0}

    def factory() -> _FakeAgent:
        def _spy_run(*a, **kw):
            call_count["n"] += 1
            return "should not be called"

        agent = _FakeAgent()
        agent.run_conversation = _spy_run  # type: ignore[method-assign]
        return agent

    node = make_framework_adapt_node(agent_factory=factory)
    state = {"delivery_mode": "sample"}
    node.func(state)

    assert call_count["n"] == 0


# ---- new_dev 图集成 ----


def test_new_dev_delivery_mode_interrupts_after_precision(tmp_path) -> None:
    """new_dev 全图:invoke → design HITL → resume → ... → delivery_mode HITL。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(["analyze", "design", "codegen", "LGTM"])

    runner = build_new_dev_graph(store=store, agent_factory=factory)
    # 第一次:invoke 跑到 design 中断
    state = runner.invoke("trivial add op", thread_id="t1")
    assert state["current_phase"] == "design"
    # 第二次:resume design approval,跑 codegen→...→delivery_mode HITL 中断
    state = runner.resume("t1", payload={"approved": True})
    assert state["current_phase"] == "delivery_mode"
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    pending = state["pending_confirmation"]
    assert pending["phase"] == "delivery_mode"
    assert pending["options"] == ["sample", "torch_npu", "pybind"]
    # precision 已完成
    assert "precision" in state["phase_history"]


def test_new_dev_sample_mode_completes_to_done(tmp_path) -> None:
    """sample 模式:framework_adapt 跳过,直接到 done。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(["analyze", "design", "codegen", "LGTM"])

    runner = build_new_dev_graph(store=store, agent_factory=factory)
    runner.invoke("trivial add op", thread_id="t1")
    runner.resume("t1", payload={"approved": True})  # 到 delivery_mode HITL
    state = runner.resume("t1", payload={"mode": "sample"})

    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE
    assert state["delivery_mode"] == "sample"
    # framework_adapt 跑了但跳过
    assert state["framework_adapt_result"]["skipped"] is True
    expected_tail = ["precision", "delivery_mode", "framework_adapt", "done"]
    assert state["phase_history"][-len(expected_tail) :] == expected_tail


def test_new_dev_torch_npu_mode_runs_framework_adapt(tmp_path) -> None:
    """torch_npu 模式:framework_adapt 真跑 LLM(再多一个 response)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from(["analyze", "design", "codegen", "LGTM", "framework adapt code"])

    runner = build_new_dev_graph(store=store, agent_factory=factory)
    runner.invoke("add op", thread_id="t1")
    runner.resume("t1", payload={"approved": True})
    state = runner.resume("t1", payload={"mode": "torch_npu"})

    assert state["current_phase"] == "done"
    assert state["delivery_mode"] == "torch_npu"
    assert state["framework_adapt_result"]["skipped"] is False
    assert state["framework_adapt_result"]["mode"] == "torch_npu"


# ---- migration 图集成 ----


def test_cuda_migration_delivery_mode_interrupts(tmp_path) -> None:
    """cuda 迁移:同样跑到 delivery_mode HITL 中断。"""
    cuda_json = """```json
{"op_info": {"name": "add", "migration_strategy": "cuda_to_ascendc", "ref_code_type": "cuda"},
 "arch_mapping": {"source_type": "cuda", "mappings": {"a": "b"}}}
```"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from([cuda_json, "design", "codegen", "LGTM"])

    runner = build_migration_graph(store=store, source_type="cuda", agent_factory=factory)
    runner.invoke("cuda code", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    state = store.load("t1")
    assert state["current_phase"] == "delivery_mode"
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM


def test_triton_migration_pybind_mode_completes(tmp_path) -> None:
    """triton 迁移:pybind 模式跑通,framework_adapt 跳过。"""
    triton_json = """```json
{"op_info": {"name": "softmax", "migration_strategy": "triton_to_ascendc", "ref_code_type": "triton"},
 "arch_mapping": {"source_type": "triton", "mappings": {"a": "b"}}}
```"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_from([triton_json, "design", "codegen", "LGTM"])

    runner = build_migration_graph(store=store, source_type="triton", agent_factory=factory)
    runner.invoke("triton code", thread_id="t1")
    runner.resume("t1", payload={"approved": True})
    state = runner.resume("t1", payload={"mode": "pybind"})

    assert state["current_phase"] == "done"
    assert state["delivery_mode"] == "pybind"
    assert state["framework_adapt_result"]["skipped"] is True
