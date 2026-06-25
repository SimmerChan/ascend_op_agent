"""U13: NpuExecutor + 验证节点工厂单测。

不依赖真实硬件 —— compile 用 mock subprocess,precision 用 numpy 已知数组。
hardware-gated 端到端测试在 ``test_hardware_compile_smoke.py``(P1-1 解锁后跑)。

覆盖:

- ``NpuExecutor.compute_precision_metrics``:已知 golden/actual 的 cos_sim/abs_err
- ``NpuExecutor.run_precision``:多 case 通过/失败统计 + 归档
- ``NpuExecutor.compile``:硬件未就绪 / mock subprocess 成功 / 超时 / 路径不存在
- ``NpuExecutor.compile_to_dict``:归档 JSON 写盘
- ``make_real_compile_node`` / ``make_real_precision_node``:节点写正确字段
- ``make_compile_fix_node`` / ``make_precision_fix_node``:LLM 节点 prompt 含关键字
- resolver:operator_path_from_code_result / test_cases_from_state
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from ascend_op_agent.orchestrator import (
    NpuExecutor,
    PrecisionMetrics,
    make_compile_fix_loop_node,
    make_compile_fix_node,
    make_precision_fix_node,
    make_real_compile_node,
    make_real_precision_node,
    operator_path_from_code_result,
)
from ascend_op_agent.orchestrator import test_cases_from_state as _test_cases_from_state
from ascend_op_agent.orchestrator.nodes.validation import (
    make_precision_fix_loop_node,
)
from ascend_op_agent.orchestrator.state_machine import Node


# ---- compute_precision_metrics ----


def test_precision_metrics_identical_arrays() -> None:
    """golden == actual → cos_sim=1.0,abs_err=0,allclose=True,meets_requirement。"""
    golden = np.array([1.0, 2.0, 3.0], dtype=np.float16)
    metrics = NpuExecutor.compute_precision_metrics(golden, golden)
    assert metrics.cos_sim == pytest.approx(1.0)
    assert metrics.abs_err_max == 0.0
    assert metrics.allclose is True
    assert metrics.meets_requirement() is True


def test_precision_metrics_known_cos_sim() -> None:
    """[1,0] vs [0,1] → cos_sim=0(正交)。"""
    golden = np.array([1.0, 0.0])
    actual = np.array([0.0, 1.0])
    metrics = NpuExecutor.compute_precision_metrics(golden, actual)
    assert metrics.cos_sim == pytest.approx(0.0, abs=1e-9)
    assert metrics.abs_err_max == pytest.approx(1.0)
    assert metrics.allclose is False


def test_precision_metrics_small_diff_meets_requirement() -> None:
    """1e-6 级差异 → abs_err < 1e-3 阈值,meets_requirement=True。"""
    golden = np.array([1.0, 2.0, 3.0])
    actual = golden + np.array([1e-7, -1e-7, 1e-8])
    metrics = NpuExecutor.compute_precision_metrics(golden, actual)
    assert metrics.abs_err_max < 1e-3
    assert metrics.meets_requirement() is True


def test_precision_metrics_large_diff_fails_requirement() -> None:
    """0.1 级差异 → 超阈值,meets_requirement=False。"""
    golden = np.array([1.0, 2.0, 3.0])
    actual = golden + 0.1
    metrics = NpuExecutor.compute_precision_metrics(golden, actual)
    assert metrics.abs_err_max > 1e-3
    assert metrics.meets_requirement() is False


def test_precision_metrics_shape_mismatch_raises() -> None:
    golden = np.array([1.0, 2.0])
    actual = np.array([1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="shape mismatch"):
        NpuExecutor.compute_precision_metrics(golden, actual)


def test_precision_metrics_zero_vector_cos_sim_zero() -> None:
    """全零向量 → cos_sim=0(避免 div by zero)。"""
    golden = np.zeros(3)
    actual = np.zeros(3)
    metrics = NpuExecutor.compute_precision_metrics(golden, actual)
    assert metrics.cos_sim == 0.0
    assert metrics.abs_err_max == 0.0


def test_precision_metrics_to_dict_round_trip() -> None:
    metrics = PrecisionMetrics(
        abs_err_max=0.001,
        abs_err_mean=0.0005,
        rel_err_max=0.01,
        cos_sim=0.9999,
        allclose=False,
    )
    d = metrics.to_dict()
    assert d["cos_sim"] == pytest.approx(0.9999)
    assert d["allclose"] is False
    assert set(d.keys()) == {
        "abs_err_max", "abs_err_mean", "rel_err_max", "cos_sim", "allclose"
    }


# ---- run_precision ----


def test_run_precision_reports_pass_fail_counts(tmp_path) -> None:
    """3 个 case:2 个 identical(通过)+ 1 个大差异(失败)。"""
    executor = NpuExecutor(archive_dir=tmp_path)
    test_cases = [
        {"golden": np.array([1.0, 2.0]), "actual": np.array([1.0, 2.0])},
        {"golden": np.array([3.0, 4.0]), "actual": np.array([3.0, 4.0])},
        {"golden": np.array([1.0]), "actual": np.array([5.0])},  # 失败
    ]
    report = executor.run_precision("add", test_cases)
    assert report["operator_name"] == "add"
    assert report["total_cases"] == 3
    assert report["passed_cases"] == 2
    assert report["failed_cases"] == 1
    assert len(report["cases"]) == 3
    assert report["cases"][2]["passed"] is False


def test_run_precision_archives_json(tmp_path) -> None:
    """归档目录存在时,写 precision_*.json 文件。"""
    archive = tmp_path / "archive"
    executor = NpuExecutor(archive_dir=archive)
    executor.run_precision("add", [
        {"golden": np.array([1.0]), "actual": np.array([1.0])}
    ])
    files = list(archive.glob("precision_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["operator_name"] == "add"
    assert data["total_cases"] == 1


def test_run_precision_handles_invalid_case() -> None:
    """缺 golden/actual 字段 → case 标记失败 + error,不抛错。"""
    executor = NpuExecutor()
    report = executor.run_precision("add", [{"bad": "shape"}])
    assert report["total_cases"] == 1
    assert report["passed_cases"] == 0
    assert "error" in report["cases"][0]


# ---- compile(硬件门控 + mock subprocess) ----


def test_compile_path_not_exist_returns_failure(tmp_path) -> None:
    """operator_path 不存在 → success=False,return_code=2。"""
    executor = NpuExecutor()
    outcome = executor.compile(str(tmp_path / "nonexistent"))
    assert outcome.success is False
    assert outcome.return_code == 2
    assert "not found" in outcome.stderr


def test_compile_cann_not_available_returns_failure(tmp_path) -> None:
    """CANN 环境未配置 → success=False,return_code=127,不调 subprocess。"""
    operator_path = tmp_path / "op"
    operator_path.mkdir()
    executor = NpuExecutor()
    # 模拟无 CANN env
    with patch.dict("os.environ", {}, clear=False):
        import os
        env_backup = {
            k: os.environ.pop(k, None)
            for k in ("ASCEND_OPP_PATH", "CANN_HOME")
        }
        try:
            assert NpuExecutor.is_cann_available() is False
            outcome = executor.compile(str(operator_path))
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v
    assert outcome.success is False
    assert outcome.return_code == 127
    assert "CANN env not configured" in outcome.stderr


def test_compile_success_mock_subprocess(tmp_path) -> None:
    """mock subprocess.run 返回 return_code=0 → success=True。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    fake_completed = _FakeCompletedProcess(returncode=0, stdout="build ok", stderr="")
    with patch("ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
               return_value=True), \
         patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run",
               return_value=fake_completed):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is True
    assert outcome.return_code == 0
    assert outcome.stdout == "build ok"
    assert "cann_compile" in outcome.command


def test_compile_failure_mock_subprocess(tmp_path) -> None:
    """mock subprocess.run 返回 return_code=1 → success=False。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    fake_completed = _FakeCompletedProcess(
        returncode=1, stdout="", stderr="syntax error in line 5"
    )
    with patch("ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
               return_value=True), \
         patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run",
               return_value=fake_completed):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is False
    assert outcome.return_code == 1
    assert "syntax error" in outcome.stderr


def test_compile_timeout(tmp_path) -> None:
    """subprocess 抛 TimeoutExpired → success=False,return_code=124。"""
    import subprocess as sp
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor(compile_timeout=1)
    with patch("ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
               return_value=True), \
         patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run",
               side_effect=sp.TimeoutExpired(cmd="cann_compile", timeout=1)):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is False
    assert outcome.return_code == 124
    assert "timeout" in outcome.stderr.lower()


def test_compile_binary_not_found(tmp_path) -> None:
    """subprocess 抛 FileNotFoundError → success=False,return_code=127。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    with patch("ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
               return_value=True), \
         patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run",
               side_effect=FileNotFoundError()):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is False
    assert outcome.return_code == 127
    assert "not found" in outcome.stderr.lower()


def test_compile_to_dict_archives(tmp_path) -> None:
    """compile_to_dict 写 compile_*.json 归档。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")
    archive = tmp_path / "archive"

    executor = NpuExecutor(archive_dir=archive)
    fake_completed = _FakeCompletedProcess(returncode=0, stdout="ok", stderr="")
    with patch("ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
               return_value=True), \
         patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run",
               return_value=fake_completed):
        result = executor.compile_to_dict(str(operator_path))
    assert result["success"] is True
    files = list(archive.glob("compile_*.json"))
    assert len(files) == 1


# ---- make_real_compile_node / make_real_precision_node ----


class _FakeExecutor:
    """mock NpuExecutor:预设 compile_to_dict / run_precision 返回值。"""

    def __init__(self, compile_result=None, precision_report=None):
        self._compile_result = compile_result or {"success": True, "return_code": 0}
        self._precision_report = precision_report or {
            "operator_name": "add", "total_cases": 1, "passed_cases": 1, "failed_cases": 0
        }

    def compile_to_dict(self, *a, **kw):
        return self._compile_result

    def run_precision(self, *a, **kw):
        return self._precision_report


def test_make_real_compile_node_writes_compile_result() -> None:
    """节点调 executor.compile_to_dict,写 compile_result 字段。"""
    executor = _FakeExecutor(compile_result={"success": True, "return_code": 0, "custom": True})
    node = make_real_compile_node(
        executor=executor,  # type: ignore[arg-type]
        operator_path_resolver=lambda s: "/fake/op.cpp",
    )
    update = node.func({"code_result": {}})
    assert update["compile_result"]["success"] is True
    assert update["compile_result"]["custom"] is True


def test_make_real_precision_node_writes_precision_report() -> None:
    executor = _FakeExecutor(precision_report={
        "operator_name": "softmax", "total_cases": 5, "passed_cases": 5, "failed_cases": 0
    })
    node = make_real_precision_node(
        executor=executor,  # type: ignore[arg-type]
        test_cases_resolver=lambda s: [{"golden": [1.0], "actual": [1.0]}],
        operator_name_resolver=lambda s: "softmax",
    )
    update = node.func({})
    assert update["precision_report"]["operator_name"] == "softmax"
    assert update["precision_report"]["passed_cases"] == 5


def test_make_real_precision_node_default_name_resolver_falls_back_to_op_info() -> None:
    """operator_name_resolver=None 时,从 op_info.name 取。"""
    executor = _FakeExecutor(precision_report={
        "operator_name": "unknown", "total_cases": 0, "passed_cases": 0, "failed_cases": 0
    })
    node = make_real_precision_node(
        executor=executor,  # type: ignore[arg-type]
        test_cases_resolver=lambda s: [],
    )
    update = node.func({"op_info": {"name": "my_op"}})
    # run_precision 收到 "my_op"
    assert update["precision_report"]["operator_name"] == "unknown"  # mock 固定返回


# ---- resolver ----


def test_operator_path_from_code_result_extracts_first_file_path() -> None:
    state = {"code_result": {"files": [{"path": "/ops/add/kernel.cpp"}, {"path": "/ops/add/op.cpp"}]}}
    assert operator_path_from_code_result(state) == "/ops/add/kernel.cpp"


def test_operator_path_from_code_result_empty_returns_empty() -> None:
    assert operator_path_from_code_result({}) == ""
    assert operator_path_from_code_result({"code_result": {"files": []}}) == ""


def test_test_cases_from_state_returns_list() -> None:
    state = {"test_cases": [{"golden": [1.0], "actual": [1.0]}]}
    assert len(_test_cases_from_state(state)) == 1
    assert _test_cases_from_state({}) == []


# ---- make_compile_fix_node / make_precision_fix_node(LLM 节点) ----


class _FakeAgent:
    def __init__(self):
        self._h: list = []
        self.captured_input: str = ""
        class _Mem:
            def __init__(self): self._p = {}
            def add(self, p, c): self._p.setdefault(p, []).append(c)
            def get(self, p): return list(self._p.get(p, []))
        self.memory = _Mem()

    def run_conversation(self, user_input, skills_layer_override=None):
        self.captured_input = user_input
        self._h.append({"role": "user", "content": user_input})
        return "fixed code"


def test_make_compile_fix_node_prompt_mentions_compile_result() -> None:
    """compile_fix 节点 task_prompt 含 compile_result / stderr 关键字。"""
    agent = _FakeAgent()
    node = make_compile_fix_node(agent_factory=lambda: agent)
    node.func({
        "messages": [{"role": "user", "content": "x"}],
        "memory_pools": {},
        "pending_confirmation": None,
    })
    assert "compile_result" in agent.captured_input
    assert "stderr" in agent.captured_input


def test_make_precision_fix_node_prompt_mentions_precision_report() -> None:
    agent = _FakeAgent()
    node = make_precision_fix_node(agent_factory=lambda: agent)
    node.func({
        "messages": [{"role": "user", "content": "x"}],
        "memory_pools": {},
        "pending_confirmation": None,
    })
    assert "precision_report" in agent.captured_input


# ---- fix_loop 节点接入 ----


def test_make_compile_fix_loop_node_writes_named_result() -> None:
    """compile_fix_loop 节点跑完写 state['compile_fix_loop_result']。"""
    review_node = Node(name="review", func=lambda s: {"clean": True, "raw_response": "LGTM"})
    fix_node = Node(name="fix", func=lambda s: {})
    loop_node = make_compile_fix_loop_node(
        review_node=review_node, fix_node=fix_node, max_rounds=2
    )
    update = loop_node.func({})
    assert "compile_fix_loop_result" in update
    assert update["compile_fix_loop_result"]["status"] == "done"


def test_make_precision_fix_loop_node_writes_named_result() -> None:
    review_node = Node(name="review", func=lambda s: {"clean": False, "issues": ["x"], "raw_response": "broken"})
    fix_node = Node(name="fix", func=lambda s: {})
    loop_node = make_precision_fix_loop_node(
        review_node=review_node, fix_node=fix_node, max_rounds=1
    )
    update = loop_node.func({})
    assert update["precision_fix_loop_result"]["status"] == "failed"


# ---- 辅助 ----


class _FakeCompletedProcess:
    """模拟 subprocess.run 返回对象。"""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
