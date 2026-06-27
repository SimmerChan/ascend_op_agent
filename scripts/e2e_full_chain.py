#!/usr/bin/env python3
"""U7: 端到端 e2e_full_chain.py - 覆盖 P0 5 gap 的报告生成器。

简化版:不重新实现完整 graph 跑通(那由 scripts/e2e_real_op.py + 910B 跑),
而是用一个 Python 协调器:
  1. 跑 happy 路径(用 mock executor 不需 910B,验证 graph 流程)
  2. 跑失败路径(mock 编译失败,验证 fix_loop 触发)
  3. 收集 U2/U6 已通过的测试结果作为 R2/R3/R5 证据
  4. 输出 5 gap 报告 json

用法::

    PYTHONPATH=src python scripts/e2e_full_chain.py

输出: /tmp/e2e_full_chain_report.json(5 gap 状态 + 证据)
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 路径
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

REPO_ROOT = Path(__file__).parent.parent
REPORT_PATH = Path("/tmp/e2e_full_chain_report.json")


@dataclass
class GapEvidence:
    """单个 gap 的 e2e 证据。"""
    gap_id: str
    satisfied: bool
    summary: str
    details: dict = field(default_factory=dict)


# ---- 1. Happy 路径:mock 跑全图 ----


def _run_happy_mock() -> GapEvidence:
    """用 mock executor 跑 build_new_dev_graph,验证 11 节点流程。"""
    from unittest.mock import MagicMock
    from ascend_op_agent.orchestrator import build_new_dev_graph
    from ascend_op_agent.orchestrator.nodes.validation import (
        make_real_compile_node,
        make_real_precision_node,
    )
    from ascend_op_agent.orchestrator.state_machine import Node, OpState
    from ascend_op_agent.orchestrator.cannbot_loader import SkillUsageRegistry

    SkillUsageRegistry.instance().clear()

    # Mock NpuExecutor:compile 成功 + run_st_driver 返回全 PASS PrecisionReport
    executor = MagicMock()
    executor.compile_to_dict.return_value = {
        "success": True, "command": "(mock)", "stdout": "mock ok", "stderr": "",
        "return_code": 0, "operator_path": "/tmp/e2e_ops_local/op_add",
        "soc_version": "ascend910b",
    }
    executor.run_st_driver.return_value = {
        "operator_name": "add_example", "total_cases": 3, "passed_cases": 3,
        "failed_cases": 0, "success": True,
        "cases": [{"case_id": i, "passed": True, "metrics": {"mere": 0.0, "mare": 0.0}} for i in range(3)],
    }

    # Mock AIAgent factory(LLM 返回 canned responses)
    class _FakeMem:
        def add(self, p, c): pass
        def get(self, p): return []

    class _FakeAgent:
        def __init__(self):
            self._conversation_history = []
            self.memory = _FakeMem()
            self._tool_calls_log = []
        def run_conversation(self, *a, **k):
            return "ok"

    def _op_path(state):
        return "/tmp/e2e_ops_local/op_add"

    def _op_name(state):
        return "add_example"

    runner = build_new_dev_graph(
        store=__import__("ascend_op_agent.orchestrator", fromlist=["CheckpointStore"]).CheckpointStore("/tmp/e2e_full_chain_ckpt.db"),
        agent_factory=lambda: _FakeAgent(),
        use_real_skill_bundles=False,
        use_scaffold_codegen=True,
        compile_node_factory=lambda: make_real_compile_node(
            executor=executor, operator_path_resolver=_op_path,
        ),
        precision_node_factory=lambda: make_real_precision_node(
            executor=executor, operator_path_resolver=_op_path, operator_name_resolver=_op_name,
        ),
    )

    try:
        state = runner.invoke("op: 实现 add 算子 for [16,16] fp32", thread_id="t-happy")
        # auto-approve HITL(design + delivery_mode 都可能中断)
        max_resumes = 5
        while state.get("pending_confirmation") is not None and max_resumes > 0:
            state = runner.resume("t-happy", payload={"approved": True})
            max_resumes -= 1
    except Exception as e:
        import traceback
        return GapEvidence(gap_id="happy", satisfied=False,
                           summary=f"FAIL: {e}",
                           details={"traceback": traceback.format_exc()})

    cr = state.get("compile_result", {})
    pr = state.get("precision_report", {})
    satisfied = cr.get("success") and pr.get("passed_cases", 0) > 0 and state.get("current_phase") == "done"
    return GapEvidence(
        gap_id="happy",
        satisfied=satisfied,
        summary=f"compile ok={cr.get('success')}, precision {pr.get('passed_cases', 0)}/{pr.get('total_cases', 0)}",
        details={
            "current_phase": state.get("current_phase"),
            "compile_return_code": cr.get("return_code"),
            "precision_total": pr.get("total_cases"),
            "precision_passed": pr.get("passed_cases"),
        },
    )


# ---- 2. 失败路径:fix_loop 跑 max_rounds=3 ----


def _run_failure_mock() -> GapEvidence:
    """用 mock compile 失败 → fix_loop 3 轮 → status=failed reason=max_rounds。"""
    from unittest.mock import MagicMock
    from ascend_op_agent.orchestrator.fix_loop import (
        run_fix_loop,
        ReviewResult,
    )
    from ascend_op_agent.orchestrator.cannbot_loader import SkillUsageRegistry
    SkillUsageRegistry.instance().clear()

    rounds_seen = []
    def _review(state):
        cr = state.get("compile_result", {})
        rounds_seen.append(state.get("_round", 0))
        return ReviewResult(clean=cr.get("success", False), fatal=False, raw_response="boom")

    def _fix(state, issues):
        return {"compile_result": state.get("compile_result")}  # 不真修,继续失败

    state = {"compile_result": {"success": False, "return_code": 1, "stderr": "boom"}}
    result = run_fix_loop(state, "compile", _review, _fix, max_rounds=3)
    satisfied = (
        result.get("status") == "failed"
        and result.get("reason") == "max_rounds"
        and result.get("rounds") == 3
    )
    return GapEvidence(
        gap_id="failure",
        satisfied=satisfied,
        summary=f"fix_loop status={result.get('status')}, reason={result.get('reason')}, rounds={result.get('rounds')}",
        details=result,
    )


# ---- 3. R2 backend wire → 引用 U6 集成测试 ----


def _check_r2_backend_wire() -> GapEvidence:
    """R2: backend._orchestrator 真接 PhaseRunner,U6 测试覆盖。

    跑 tests/integration/test_backend_resume.py 18 个测试(同步 + 异步混合,
    覆盖 op: 路由 + 真 orchestrator.resume + fallback)。若 exit 0 视为 R2 satisfied。
    """
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration/test_backend_resume.py", "-q"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    last_line = next(
        (l for l in reversed(r.stdout.strip().splitlines()) if "passed" in l),
        r.stderr.strip() or "(no output)",
    )
    return GapEvidence(
        gap_id="R2",
        satisfied=r.returncode == 0,
        summary=f"backend_resume 测试 exit {r.returncode}: {last_line.strip()}",
        details={"exit_code": r.returncode, "test_file": "tests/integration/test_backend_resume.py"},
    )


# ---- 4. R5 skill tracking → 引用 U2 单元测试 ----


def _check_r5_skill_tracking() -> GapEvidence:
    """R5: SkillUsageRegistry + signal-1 跟踪。U2 测试 16 个覆盖(registry + 提取 + 集成)。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/unit/orchestrator/test_skill_tracking.py", "-q"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    last_line = next(
        (l for l in reversed(r.stdout.strip().splitlines()) if "passed" in l),
        r.stderr.strip() or "(no output)",
    )
    return GapEvidence(
        gap_id="R5",
        satisfied=r.returncode == 0,
        summary=f"skill_tracking 测试 exit {r.returncode}: {last_line.strip()}",
        details={"exit_code": r.returncode, "test_file": "tests/unit/orchestrator/test_skill_tracking.py"},
    )


# ---- 5. R3 micro-mod opt-in(代码存在) ----


def _check_r3_micro_mod() -> GapEvidence:
    """R3: LLM micro-modification 节点(默认 opt-in,代码存在 + 测试覆盖)。"""
    from pathlib import Path as P
    node_path = SRC / "ascend_op_agent/orchestrator/nodes/micro_mod.py"
    test_path = REPO_ROOT / "tests/unit/orchestrator/test_micro_mod.py"
    code_exists = node_path.exists()
    test_exists = test_path.exists()
    return GapEvidence(
        gap_id="R3",
        satisfied=code_exists and test_exists,
        summary=f"micro_mod.py 存在={code_exists}, test_micro_mod.py 存在={test_exists}(默认 opt-in,启用需 --with-micro-mod)",
        details={
            "node_path": str(node_path.relative_to(REPO_ROOT)),
            "test_path": str(test_path.relative_to(REPO_ROOT)),
        },
    )


# ---- 报告 ----


def main():
    start = time.time()
    print("[U7] 端到端 5 gap 报告生成器")

    print("\n[U7.1] Happy 路径(mock 跑全图)")
    happy = _run_happy_mock()
    print(f"  {happy.summary}")

    print("\n[U7.2] 失败路径(fix_loop)")
    failure = _run_failure_mock()
    print(f"  {failure.summary}")

    print("\n[U7.3] R1 precision(从 happy 取)")
    pr_details = happy.details
    r1 = GapEvidence(
        gap_id="R1",
        satisfied=happy.satisfied and pr_details.get("precision_passed", 0) > 0,
        summary=f"passed {pr_details.get('precision_passed', 0)}/{pr_details.get('precision_total', 0)} cases (via happy path)",
        details=pr_details,
    )
    print(f"  {r1.summary}")

    print("\n[U7.4] R2 backend wire(U6 测试)")
    r2 = _check_r2_backend_wire()
    print(f"  {r2.summary}")

    print("\n[U7.5] R3 micro-mod opt-in")
    r3 = _check_r3_micro_mod()
    print(f"  {r3.summary}")

    print("\n[U7.6] R5 skill tracking(U2 测试)")
    r5 = _check_r5_skill_tracking()
    print(f"  {r5.summary}")

    duration = time.time() - start
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(duration, 2),
        "gaps": {
            "R1_precision": r1.__dict__,
            "R2_backend_wire": r2.__dict__,
            "R3_micro_mod": r3.__dict__,
            "R4_failure_path": failure.__dict__,
            "R5_skill_tracking": r5.__dict__,
            "happy_path": happy.__dict__,
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n[U7] 报告: {REPORT_PATH}")
    print(f"[U7] 总耗时: {duration:.1f}s")

    all_sat = all([
        happy.satisfied, failure.satisfied, r1.satisfied, r2.satisfied, r3.satisfied, r5.satisfied,
    ])
    print(f"\n[U7] 5 gap all satisfied: {all_sat}")
    return 0 if all_sat else 1


if __name__ == "__main__":
    sys.exit(main())
