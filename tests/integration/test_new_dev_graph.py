"""U9: Path-C 新开发图集成测试。

覆盖:

- 全阶段顺序执行(entry → analyze → design → codegen → review_fix → compile →
  precision → done)
- design HITL 中断(产 __interrupt__)+ resume 推进
- compile / precision 占位节点在 U9 阶段工作(U13 替换为真)
- skill_bundles 注入到各阶段(skills_layer_override)
- phase_callback 收到所有阶段事件
- 自定义 compile_node_factory 替换占位(U13 集成测试场景)

mock agent_factory 避开真实 LLM 调用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ascend_op_agent.orchestrator import (
    STATUS_DONE,
    STATUS_WAITING_CONFIRM,
    CheckpointStore,
    Node,
    build_new_dev_graph,
)


class FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class FakeAgent:
    """Mock AIAgent(同 test_orchestrator_smoke.py)。"""

    def __init__(self) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = FakeMemory()
        self.last_skills_override: Any = None

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override: Any = None,
        *,
        task_type: Any = None,
        no_tools=False,
    ) -> str:
        self.last_skills_override = skills_layer_override
        self._conversation_history.append({"role": "user", "content": user_input})
        response = f"resp({user_input[:30]})"
        self._conversation_history.append({"role": "assistant", "content": response})
        return response


def _factory() -> FakeAgent:
    return FakeAgent()


# ---- 全阶段跑通 ----


def test_scaffold_codegen_single_node_path_REMOVED(tmp_path) -> None:
    """U4 重构删除了 use_scaffold_codegen 单节点路径(统一为 scaffold 注入 + LLM 语义
    多节点)。原测试期望单 codegen 节点,U4 后多节点由 test_new_dev_runs_all_phases
    覆盖。占位避免 import os 未用,真实断言移至 test_new_dev_runs_all_phases。"""
    assert True


def test_new_dev_runs_all_phases_after_design_approval(tmp_path) -> None:
    """U9 MVP:design HITL 批准后,全节点顺序跑通,checkpoint 落盘。

    design 是 HITL 节点(必须 invoke → resume 才能跑完)。
    U12 后新增 delivery_mode HITL,需要再 resume 一次选交付模式。
    """
    store = CheckpointStore(tmp_path / "ck.db")

    runner = build_new_dev_graph(store=store, agent_factory=_factory)
    runner.invoke("design a trivial add op", thread_id="t1")
    runner.resume("t1", payload={"approved": True})
    state = runner.resume("t1", payload={"mode": "sample"})

    # 全阶段访问(codegen 默认 5 个独立 LLM 节点)
    expected = [
        "entry",
        "analyze",
        "design",
        "codegen_scaffold",
        "codegen_kernel",
        "codegen_host",
        "codegen_proto",
        "review_fix",
        "compile",
        "precision",
        "delivery_mode",
        "framework_adapt",
        "done",
    ]
    assert state["phase_history"] == expected
    assert state["current_phase"] == "done"

    # 占位 compile / precision 输出
    assert state["compile_result"]["success"] is True
    assert state["precision_report"]["operator_name"] == "placeholder"

    # checkpoint
    assert store.get_status("t1") == STATUS_DONE


# ---- design HITL 中断 ----


def test_design_node_interrupts_for_hitl(tmp_path) -> None:
    """design 节点跑完 LLM → __interrupt__,等用户批准。"""
    store = CheckpointStore(tmp_path / "ck.db")
    runner = build_new_dev_graph(store=store, agent_factory=_factory)

    state = runner.invoke("design add op", thread_id="t1")

    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    assert state["current_phase"] == "design"
    # design 之前阶段已访问
    assert "entry" in state["phase_history"]
    assert "analyze" in state["phase_history"]
    assert "design" not in state["phase_history"]  # 中断不算完成
    # 中断 payload
    pending = state["pending_confirmation"]
    assert pending["phase"] == "design"
    assert "approve" in pending["options"]


def test_resume_after_design_approval_completes_all_phases(tmp_path) -> None:
    """design 批准后 → codegen → review_fix → compile → precision →
    delivery_mode HITL → resume(sample) → framework_adapt → done。"""
    store = CheckpointStore(tmp_path / "ck.db")
    runner = build_new_dev_graph(store=store, agent_factory=_factory)

    # 第一跑:invoke 到 design 中断
    state = runner.invoke("design add op", thread_id="t1")
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM

    # 第二跑:resume with approval → 跑到 delivery_mode HITL 中断
    state = runner.resume("t1", payload={"approved": True})
    assert state["current_phase"] == "delivery_mode"
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM

    # 第三跑:resume with mode → 到 done
    state = runner.resume("t1", payload={"mode": "sample"})

    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE
    expected = [
        "entry",
        "analyze",
        "design",
        "codegen_scaffold",
        "codegen_kernel",
        "codegen_host",
        "codegen_proto",
        "review_fix",
        "compile",
        "precision",
        "delivery_mode",
        "framework_adapt",
        "done",
    ]
    assert state["phase_history"] == expected


def test_pending_confirmation_cleared_after_resume(tmp_path) -> None:
    """resume 后 pending_confirmation 被节点主动清除(design + delivery_mode 各一次)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    runner = build_new_dev_graph(store=store, agent_factory=_factory)

    runner.invoke("design add op", thread_id="t1")
    state = runner.resume("t1", payload={"approved": True})
    # design cleared,delivery_mode 写入(因为 delivery_mode 是新的 HITL)
    assert state["pending_confirmation"]["phase"] == "delivery_mode"
    state = runner.resume("t1", payload={"mode": "sample"})
    assert state["pending_confirmation"] is None


# ---- skill bundle 注入 ----


def test_skill_bundles_passed_to_each_phase(tmp_path) -> None:
    """skill_bundles 参数注入到各阶段 LLM 节点的 skills_layer_override。"""
    store = CheckpointStore(tmp_path / "ck.db")

    seen_overrides: list[Any] = []
    captured_factory = _factory

    def spying_factory() -> FakeAgent:
        a = captured_factory()
        orig_run = a.run_conversation

        def _spy_run(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
            no_tools=False,
        ):
            seen_overrides.append(skills_layer_override)
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        a.run_conversation = _spy_run  # type: ignore[method-assign]
        return a

    bundles = {
        "analyze": "## Skill: ascendc-tiling-design",
        "design": "## Skill: ascendc-kernel-architect",
        "codegen": "## Skill: ascendc-kernel-developer",
        "review_fix": "## Skill: ascendc-kernel-reviewer",
    }
    runner = build_new_dev_graph(
        store=store,
        agent_factory=spying_factory,
        skill_bundles=bundles,
    )
    runner.invoke("design add op", thread_id="t1")
    # invoke 在 design 中断,所以只看到 analyze + design 的 override
    # (analyze 和 design 是 invoke 内会跑到的阶段)
    assert "## Skill: ascendc-tiling-design" in seen_overrides
    assert "## Skill: ascendc-kernel-architect" in seen_overrides


# ---- phase_callback ----


def test_phase_callback_emits_events_for_all_phases(tmp_path) -> None:
    """phase_callback:每阶段 started + completed,interrupt 后 design 是 interrupted。"""
    store = CheckpointStore(tmp_path / "ck.db")
    events: list[tuple[str, str]] = []

    runner = build_new_dev_graph(
        store=store,
        agent_factory=_factory,
        phase_callback=lambda phase, event, error=None: events.append((phase, event)),
    )
    runner.invoke("design add op", thread_id="t1")

    # entry / analyze 完整 started+completed
    assert ("entry", "started") in events
    assert ("entry", "completed") in events
    assert ("analyze", "started") in events
    assert ("analyze", "completed") in events
    # design:started 但 interrupted(不是 completed)
    assert ("design", "started") in events
    assert ("design", "interrupted") in events
    assert ("design", "completed") not in events


# ---- 自定义 compile_node_factory(U13 集成入口) ----


def test_custom_compile_node_factory_replaces_placeholder(tmp_path) -> None:
    """U13 集成测试场景:注入真实 compile_node,占位被替换。"""
    store = CheckpointStore(tmp_path / "ck.db")

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

    runner = build_new_dev_graph(
        store=store,
        agent_factory=_factory,
        compile_node_factory=custom_compile_factory,
    )
    runner.invoke("design add", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    # 验证 custom compile 被调用(可从 checkpoint load 看)
    state = store.load("t1")
    assert state["compile_result"]["custom"] is True
    assert state["compile_result"]["command"] == "bash build.sh"


def test_custom_precision_node_factory_replaces_placeholder(tmp_path) -> None:
    """U13 注入真 precision_node(numpy diff)后,占位被替换。"""
    store = CheckpointStore(tmp_path / "ck.db")

    def custom_precision_factory() -> Node:
        def _real_precision(state: dict) -> dict:
            return {
                "precision_report": {
                    "operator_name": "add",
                    "total_cases": 5,
                    "passed_cases": 5,
                    "failed_cases": 0,
                    "avg_cos_sim": 0.99999,
                    "custom": True,
                },
            }

        return Node(name="precision", func=_real_precision)

    runner = build_new_dev_graph(
        store=store,
        agent_factory=_factory,
        precision_node_factory=custom_precision_factory,
    )
    runner.invoke("design add", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    state = store.load("t1")
    assert state["precision_report"]["total_cases"] == 5
    assert state["precision_report"]["custom"] is True


# ---- crash recovery ----


def test_crash_in_codegen_resumes_correctly(tmp_path) -> None:
    """codegen 节点崩溃 → resume 重跑同节点 → 推进。"""
    store = CheckpointStore(tmp_path / "ck.db")
    codegen_call_count = {"n": 0}

    def crashing_factory() -> FakeAgent:
        a = _factory()
        orig_run = a.run_conversation

        def _maybe_crash(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
            no_tools=False,
        ):
            # 仅 codegen 节点的 task_prompt 含 "developer"
            if "developer" in user_input:
                codegen_call_count["n"] += 1
                if codegen_call_count["n"] == 1:
                    raise RuntimeError("simulated codegen crash")
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        a.run_conversation = _maybe_crash  # type: ignore[method-assign]
        return a

    runner1 = build_new_dev_graph(store=store, agent_factory=crashing_factory)
    # invoke 跑到 design 中断(此时 codegen 还没跑)
    runner1.invoke("design add", thread_id="t1")
    # resume 第一次:触发 codegen 崩溃
    with pytest.raises(RuntimeError, match="simulated codegen crash"):
        runner1.resume("t1", payload={"approved": True})

    assert store.get_status("t1") == "failed"
    # resume 第二次:codegen 重跑(成功),推进到 delivery_mode HITL
    runner2 = build_new_dev_graph(store=store, agent_factory=crashing_factory)
    state = runner2.resume("t1")
    assert state["current_phase"] == "delivery_mode"
    # resume 第三次:选 sample,跑 framework_adapt(skip) + done
    state = runner2.resume("t1", payload={"mode": "sample"})
    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE
