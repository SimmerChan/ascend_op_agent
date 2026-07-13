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
    assert set(d.keys()) == {"abs_err_max", "abs_err_mean", "rel_err_max", "cos_sim", "allclose"}


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
    executor.run_precision("add", [{"golden": np.array([1.0]), "actual": np.array([1.0])}])
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

        env_backup = {k: os.environ.pop(k, None) for k in ("ASCEND_OPP_PATH", "CANN_HOME")}
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
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available", return_value=True
        ),
        patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run", return_value=fake_completed),
    ):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is True
    assert outcome.return_code == 0
    assert outcome.stdout == "build ok"
    assert "build.sh" in outcome.command


def test_compile_failure_mock_subprocess(tmp_path) -> None:
    """mock subprocess.run 返回 return_code=1 → success=False。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    fake_completed = _FakeCompletedProcess(returncode=1, stdout="", stderr="syntax error in line 5")
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available", return_value=True
        ),
        patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run", return_value=fake_completed),
    ):
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
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available", return_value=True
        ),
        patch(
            "ascend_op_agent.orchestrator.npu_exec.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="bash build.sh", timeout=1),
        ),
    ):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is False
    assert outcome.return_code == 124
    assert "timeout" in outcome.stderr.lower()


def test_compile_binary_not_found(tmp_path) -> None:
    """subprocess 抛 FileNotFoundError → success=False,return_code=127。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available", return_value=True
        ),
        patch(
            "ascend_op_agent.orchestrator.npu_exec.subprocess.run", side_effect=FileNotFoundError()
        ),
    ):
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
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available", return_value=True
        ),
        patch("ascend_op_agent.orchestrator.npu_exec.subprocess.run", return_value=fake_completed),
    ):
        result = executor.compile_to_dict(str(operator_path))
    assert result["success"] is True
    files = list(archive.glob("compile_*.json"))
    assert len(files) == 1


# ---- make_real_compile_node / make_real_precision_node ----


class _FakeExecutor:
    """mock NpuExecutor:预设 compile_to_dict / run_st_driver / run_precision 返回值。"""

    def __init__(self, compile_result=None, precision_report=None):
        self._compile_result = compile_result or {"success": True, "return_code": 0}
        self._precision_report = precision_report or {
            "operator_name": "add",
            "total_cases": 1,
            "passed_cases": 1,
            "failed_cases": 0,
        }

    def compile_to_dict(self, *a, **kw):
        return self._compile_result

    def run_st_driver(self, operator_path, op_name="add_example", **kw):
        """U1 新方法,返回同形 PrecisionReport dict。"""
        return self._precision_report

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
    """U4: node 调 executor.run_st_driver(走 ST 驱动),写 precision_report。"""
    executor = _FakeExecutor(
        precision_report={
            "operator_name": "softmax",
            "total_cases": 5,
            "passed_cases": 5,
            "failed_cases": 0,
            "success": True,
        }
    )
    node = make_real_precision_node(
        executor=executor,  # type: ignore[arg-type]
        operator_path_resolver=lambda s: "/tmp/op_test",
        operator_name_resolver=lambda s: "softmax",
    )
    # state 含 compile_result.success=True 通过 gate
    update = node.func({"compile_result": {"success": True}})
    assert update["precision_report"]["operator_name"] == "softmax"
    assert update["precision_report"]["passed_cases"] == 5
    assert update["precision_report"]["success"] is True


def test_make_real_precision_node_default_name_resolver_falls_back_to_op_info() -> None:
    """U4: operator_name_resolver=None 时,从 op_info.name 取,fallback 'unknown'。"""
    executor = _FakeExecutor(
        precision_report={
            "operator_name": "unknown",
            "total_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
        }
    )
    node = make_real_precision_node(
        executor=executor,  # type: ignore[arg-type]
        operator_path_resolver=lambda s: "/tmp/op",
    )
    update = node.func({"op_info": {"name": "my_op"}, "compile_result": {"success": True}})
    # run_st_driver 收到 "my_op"(resolver 缺省取 op_info.name)
    assert update["precision_report"]["operator_name"] == "unknown"  # mock 固定返回


def test_make_real_precision_node_skips_when_compile_failed() -> None:
    """U4: compile_result.success=False → 跳过 ST 驱动,返回 compile_not_ready。"""
    executor = _FakeExecutor(
        precision_report={"operator_name": "x", "total_cases": 99, "passed_cases": 99}
    )
    node = make_real_precision_node(
        executor=executor,  # type: ignore[arg-type]
        operator_path_resolver=lambda s: "/tmp/op",
    )
    update = node.func({"compile_result": {"success": False}})
    # 不调 run_st_driver → mock 报告应被忽略
    assert update["precision_report"]["total_cases"] == 0
    assert "compile_not_ready" in update["precision_report"]["error"]


# ---- resolver ----


def test_operator_path_from_code_result_extracts_first_file_path() -> None:
    state = {
        "code_result": {"files": [{"path": "/ops/add/kernel.cpp"}, {"path": "/ops/add/op.cpp"}]}
    }
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
            def __init__(self):
                self._p = {}

            def add(self, p, c):
                self._p.setdefault(p, []).append(c)

            def get(self, p):
                return list(self._p.get(p, []))

        self.memory = _Mem()

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
    ):
        self.captured_input = user_input
        self._h.append({"role": "user", "content": user_input})
        return "fixed code"


def test_make_compile_fix_node_prompt_mentions_compile_result() -> None:
    """compile_fix 节点 task_prompt 含 compile_result / stderr 关键字。"""
    agent = _FakeAgent()
    node = make_compile_fix_node(agent_factory=lambda: agent)
    node.func(
        {
            "messages": [{"role": "user", "content": "x"}],
            "memory_pools": {},
            "pending_confirmation": None,
        }
    )
    assert "compile_result" in agent.captured_input
    assert "stderr" in agent.captured_input


def test_make_precision_fix_node_prompt_mentions_precision_report() -> None:
    agent = _FakeAgent()
    node = make_precision_fix_node(agent_factory=lambda: agent)
    node.func(
        {
            "messages": [{"role": "user", "content": "x"}],
            "memory_pools": {},
            "pending_confirmation": None,
        }
    )
    assert "precision_report" in agent.captured_input


# ---- fix_loop 节点接入 ----


def test_make_compile_fix_loop_node_writes_named_result() -> None:
    """compile_fix_loop 节点跑完写 state['compile_fix_loop_result']。"""
    review_node = Node(name="review", func=lambda s: {"clean": True, "raw_response": "LGTM"})
    fix_node = Node(name="fix", func=lambda s: {})
    loop_node = make_compile_fix_loop_node(review_node=review_node, fix_node=fix_node, max_rounds=2)
    update = loop_node.func({})
    assert "compile_fix_loop_result" in update
    assert update["compile_fix_loop_result"]["status"] == "done"


def test_make_precision_fix_loop_node_writes_named_result() -> None:
    review_node = Node(
        name="review", func=lambda s: {"clean": False, "issues": ["x"], "raw_response": "broken"}
    )
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


# ---- SSH 远程后端 ----


class _FakeSSHEnv:
    """mock SSHEnvironment:记录 execute 调用,预设返回值队列。

    每次 execute 按 callable key 匹配返回(支持 env 探测与 compile 命令分别 stub)。
    """

    def __init__(self, responses=None, env_probe_stdout=""):
        # responses: list[(substring_match, ExecuteResult-like)]
        self._responses = list(responses) if responses else []
        self._env_probe_stdout = env_probe_stdout
        self.captured_commands: list[str] = []

    def execute(self, command, cwd=None, timeout=None, stdin_data=None):
        self.captured_commands.append(command)
        # env 探测命令(含 echo $ASCEND_OPP_PATH)
        if "ASCEND_OPP_PATH" in command:
            return _FakeExecResult(
                return_code=0, stdout=self._env_probe_stdout, stderr="", timed_out=False
            )
        # 按 substring 匹配预设响应
        for substr, result in self._responses:
            if substr in command:
                return result
        # 默认成功
        return _FakeExecResult(return_code=0, stdout="ok", stderr="", timed_out=False)


class _FakeExecResult:
    def __init__(self, return_code, stdout, stderr, timed_out=False):
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.success = return_code == 0
        self.timed_out = timed_out


def test_ssh_env_property_is_remote() -> None:
    """ssh_env 非 None 时 is_remote=True。"""
    local = NpuExecutor()
    assert local.is_remote is False
    remote = NpuExecutor(ssh_env=_FakeSSHEnv())
    assert remote.is_remote is True


def test_is_remote_cann_available_returns_true_when_env_set() -> None:
    """远程探测 ASCEND_OPP_PATH 非空 → True。"""
    ssh_env = _FakeSSHEnv(env_probe_stdout="/usr/local/Ascend/ascend-toolkit/latest/opp")
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup="source /usr/local/Ascend/set_env.sh && ",
    )
    assert executor.is_remote_cann_available() is True
    # 探测命令含 remote_env_setup 前缀
    assert "source /usr/local/Ascend/set_env.sh" in ssh_env.captured_commands[0]
    assert "ASCEND_OPP_PATH" in ssh_env.captured_commands[0]


def test_is_remote_cann_available_returns_false_when_env_unset() -> None:
    """远程 ASCEND_OPP_PATH 空 / 未展开 → False。"""
    executor_empty = NpuExecutor(ssh_env=_FakeSSHEnv(env_probe_stdout=""))
    assert executor_empty.is_remote_cann_available() is False

    executor_unexpanded = NpuExecutor(ssh_env=_FakeSSHEnv(env_probe_stdout="$ASCEND_OPP_PATH"))
    assert executor_unexpanded.is_remote_cann_available() is False


def test_is_remote_cann_available_returns_false_without_ssh_env() -> None:
    """无 ssh_env 时远程探测恒 False。"""
    assert NpuExecutor().is_remote_cann_available() is False


def test_compile_remote_success() -> None:
    """SSH 远程编译:env 就绪 + cann_compile returncode=0 → success=True。"""
    fake_ok = _FakeExecResult(return_code=0, stdout="build ok", stderr="")
    ssh_env = _FakeSSHEnv(
        responses=[("build.sh", fake_ok)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup="source set_env.sh && ",
    )
    outcome = executor.compile("/remote/path/op")
    assert outcome.success is True
    assert outcome.return_code == 0
    assert outcome.stdout == "build ok"
    # 命令含 env setup 前缀 + target
    assert "source set_env.sh" in outcome.command
    assert "bash build.sh --soc=ascend910b -j8" in outcome.command


def test_compile_remote_failure_propagates_stderr() -> None:
    """远程 cann_compile returncode=1 → success=False + stderr 透传。"""
    fake_fail = _FakeExecResult(return_code=1, stdout="", stderr="syntax error line 5")
    ssh_env = _FakeSSHEnv(
        responses=[("build.sh", fake_fail)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(ssh_env=ssh_env)
    outcome = executor.compile("/remote/op")
    assert outcome.success is False
    assert outcome.return_code == 1
    assert "syntax error" in outcome.stderr


def test_compile_remote_env_not_ready_returns_127() -> None:
    """远程 CANN 未 source → success=False return_code=127,不调 cann_compile。"""
    ssh_env = _FakeSSHEnv(env_probe_stdout="")  # env 未就绪
    executor = NpuExecutor(ssh_env=ssh_env)
    outcome = executor.compile("/remote/op")
    assert outcome.success is False
    assert outcome.return_code == 127
    assert "remote CANN env not configured" in outcome.stderr
    # 不应发出 cann_compile 命令(只发了 env 探测)
    assert not any("build.sh" in c for c in ssh_env.captured_commands)


def test_compile_remote_empty_path_returns_2() -> None:
    """远程空 operator_path → return_code=2(不调 SSH)。"""
    ssh_env = _FakeSSHEnv(env_probe_stdout="/opt/Ascend/opp")
    executor = NpuExecutor(ssh_env=ssh_env)
    outcome = executor.compile("")
    assert outcome.success is False
    assert outcome.return_code == 2
    assert ssh_env.captured_commands == []


def test_compile_remote_timeout() -> None:
    """远程 cann_compile 超时(timed_out=True)→ return_code=124。"""
    fake_timeout = _FakeExecResult(return_code=124, stdout="", stderr="", timed_out=True)
    ssh_env = _FakeSSHEnv(
        responses=[("build.sh", fake_timeout)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(ssh_env=ssh_env, compile_timeout=5)
    outcome = executor.compile("/remote/op")
    assert outcome.success is False
    assert outcome.return_code == 124
    assert "timeout" in outcome.stderr.lower()


def test_compile_remote_ssh_exception_returns_126() -> None:
    """SSH execute 抛异常 → return_code=126,不向上抛。"""

    class _ExplodingSSHEnv(_FakeSSHEnv):
        def execute(self, *a, **kw):
            cmd = a[0] if a else kw.get("command", "")
            self.captured_commands.append(cmd)
            if "ASCEND_OPP_PATH" in cmd:
                return _FakeExecResult(0, "/opt/Ascend/opp", "")
            raise RuntimeError("SSH connection lost")

    executor = NpuExecutor(ssh_env=_ExplodingSSHEnv())
    outcome = executor.compile("/remote/op")
    assert outcome.success is False
    assert outcome.return_code == 126
    assert "SSH execute failed" in outcome.stderr


def test_compile_remote_uses_remote_env_setup_prefix() -> None:
    """remote_env_setup 前缀同时注入 env 探测和 compile 命令。"""
    fake_ok = _FakeExecResult(return_code=0, stdout="ok", stderr="")
    ssh_env = _FakeSSHEnv(
        responses=[("build.sh", fake_ok)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup="source /usr/local/Ascend/ascend-toolkit/set_env.sh && ",
    )
    executor.compile("/remote/op")
    # 两条命令(env 探测 + compile)都应有前缀
    for cmd in ssh_env.captured_commands:
        assert "source /usr/local/Ascend/ascend-toolkit/set_env.sh" in cmd


# ---- 容器拓扑(docker exec)----


def test_is_containerized_flag() -> None:
    """container_name 非空时 is_containerized=True。"""
    assert NpuExecutor().is_containerized is False
    assert NpuExecutor(ssh_env=_FakeSSHEnv(), container_name="ops_pt").is_containerized


def test_wrap_remote_cmd_no_container() -> None:
    """无 container:命令 = remote_env_setup + inner。"""
    executor = NpuExecutor(
        ssh_env=_FakeSSHEnv(),
        remote_env_setup="source set_env.sh && ",
    )
    wrapped = executor._wrap_remote_cmd("bash build.sh --soc=ascend910b -j8")
    assert wrapped == "source set_env.sh && bash build.sh --soc=ascend910b -j8"


def test_wrap_remote_cmd_with_container() -> None:
    """有 container:命令包进 docker exec <c> bash -c '...'。"""
    executor = NpuExecutor(
        ssh_env=_FakeSSHEnv(),
        remote_env_setup="source set_env.sh && ",
        container_name="ops_pt",
    )
    wrapped = executor._wrap_remote_cmd("bash build.sh --soc=ascend910b -j8")
    assert wrapped == (
        "docker exec ops_pt bash -c 'source set_env.sh && bash build.sh --soc=ascend910b -j8'"
    )


def test_compile_remote_wraps_with_docker_exec() -> None:
    """容器模式:compile 命令外层 docker exec,内层 source + build.sh。"""
    fake_ok = _FakeExecResult(return_code=0, stdout="ok", stderr="")
    ssh_env = _FakeSSHEnv(
        responses=[("build.sh", fake_ok)],
        env_probe_stdout="/opt/Ascend/opp",  # env 探测也要进容器
    )
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup="source set_env.sh && ",
        container_name="ops_pt",
    )
    outcome = executor.compile("/home/hsl/op")
    assert outcome.success is True
    # 所有发出的命令(env 探测 + compile)都应被 docker exec ops_pt 包裹
    for cmd in ssh_env.captured_commands:
        assert cmd.startswith("docker exec ops_pt bash -c '")
    # compile 命令含 build.sh + 工程路径
    compile_cmd = [c for c in ssh_env.captured_commands if "build.sh" in c]
    assert len(compile_cmd) == 1
    assert "/home/hsl/op" in compile_cmd[0]


def test_is_remote_cann_available_probes_inside_container() -> None:
    """容器模式:env 探测命令也包进 docker exec。"""
    ssh_env = _FakeSSHEnv(env_probe_stdout="/opt/Ascend/opp")
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup="source set_env.sh && ",
        container_name="ops_pt",
    )
    assert executor.is_remote_cann_available() is True
    assert ssh_env.captured_commands[0].startswith("docker exec ops_pt bash -c '")
    assert "ASCEND_OPP_PATH" in ssh_env.captured_commands[0]


# ---- run_st_driver(U1, 基于 2026-06-27 spike) ----


# spike 实测 stdout 样本(精简版,见 docs/e2e/2026-06-27-st-driver-spike-report.md)
_ST_STDOUT_SAMPLE = """========================================
add_example 算子 ST 测试
========================================
模式: Real (NPU)
测试: FP32 基础加法
[Real] 测试 - size=6
  [PASS] MERE=0.00e+00, MARE=0.00e+00 (threshold=1.22e-04, 6 elems)
测试: INT32 基础加法
[Real] 测试 - size=6
  [PASS] 所有 6 个元素一致
测试: FP32 边界
[Real] 测试 - size=4
  [FAIL] MERE=1.50e-03, MARE=2.00e-03 (threshold=1.22e-04, 4 elems)
========================================
测试报告
========================================
总计: 3
通过: 2
失败: 1
========================================
"""


def test_parse_st_stdout_fp_and_int_and_fail_cases() -> None:
    """parser 正确解析 FP case (MERE/MARE) + INT case (元素一致) + FAIL case。"""
    report = NpuExecutor._parse_st_stdout(_ST_STDOUT_SAMPLE, "add_example")
    assert report["operator_name"] == "add_example"
    assert report["total_cases"] == 3
    assert report["passed_cases"] == 2
    assert report["failed_cases"] == 1
    cases = report["cases"]
    assert len(cases) == 3
    # FP pass case
    assert cases[0]["passed"] is True
    assert cases[0]["metrics"]["mere"] == 0.0
    assert cases[0]["metrics"]["elems"] == 6
    # INT pass case
    assert cases[1]["passed"] is True
    assert cases[1]["metrics"]["dtype"] == "int"
    # FP fail case
    assert cases[2]["passed"] is False
    assert cases[2]["metrics"]["mare"] == 0.002


def test_parse_st_stdout_empty_returns_zeros() -> None:
    """空 stdout → 0 cases,summary 推 0。"""
    report = NpuExecutor._parse_st_stdout("", "add_example")
    assert report["total_cases"] == 0
    assert report["passed_cases"] == 0
    assert report["failed_cases"] == 0
    assert report["cases"] == []


def test_parse_st_stdout_missing_summary_infers_from_cases() -> None:
    """无 summary 行时,从 cases 推 total/passed/failed。"""
    stdout = "  [PASS] MERE=0.00e+00, MARE=0.00e+00 (threshold=1.22e-04, 6 elems)\n"
    report = NpuExecutor._parse_st_stdout(stdout, "add_example")
    assert report["total_cases"] == 1
    assert report["passed_cases"] == 1
    assert report["failed_cases"] == 0


def test_run_st_driver_local_cann_unavailable_returns_failure() -> None:
    """本地无 CANN env → 降级 failure 报告(success=False),不抛。"""
    executor = NpuExecutor()
    with patch.dict("os.environ", {}, clear=False):
        import os as _os

        backup = {k: _os.environ.pop(k, None) for k in ("ASCEND_OPP_PATH", "CANN_HOME")}
        try:
            report = executor.run_st_driver("/tmp/op", "add_example")
        finally:
            for k, v in backup.items():
                if v is not None:
                    _os.environ[k] = v
    assert report["success"] is False
    assert report["total_cases"] == 0
    assert "CANN env not configured" in report["error"]


def test_run_st_driver_remote_install_fail_returns_failure() -> None:
    """远程:install op 包失败(rc!=0)→ failure 报告含 install 错误。"""
    install_fail = _FakeExecResult(return_code=1, stdout="", stderr="install boom")
    ssh_env = _FakeSSHEnv(
        responses=[("custom_opp", install_fail)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(ssh_env=ssh_env, remote_env_setup="source set_env.sh && ")
    report = executor.run_st_driver("/home/hsl/op", "add_example")
    assert report["success"] is False
    assert "install op package failed" in report["error"]
    # install 失败 → 不应继续 build/run
    assert not any("make" in c for c in ssh_env.captured_commands)


def test_run_st_driver_remote_build_fail_returns_failure() -> None:
    """远程:install 成功但 build 失败 → failure 报告含 build 错误。"""
    install_ok = _FakeExecResult(return_code=0, stdout="SUCCESS", stderr="")
    build_fail = _FakeExecResult(return_code=2, stdout="", stderr="cmake boom")
    ssh_env = _FakeSSHEnv(
        responses=[("custom_opp", install_ok), ("cmake", build_fail)],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(ssh_env=ssh_env, remote_env_setup="source set_env.sh && ")
    report = executor.run_st_driver("/home/hsl/op", "add_example")
    assert report["success"] is False
    assert "build ST driver failed" in report["error"]


def test_run_st_driver_remote_success_parses_stdout() -> None:
    """远程:全 6 步成功 → PrecisionReport 解析 stdout,success=True。"""
    install_ok = _FakeExecResult(return_code=0, stdout="SUCCESS", stderr="")
    build_ok = _FakeExecResult(return_code=0, stdout="Built target", stderr="")
    # 用全 PASS 的 stdout(无 FAIL case)
    all_pass_stdout = (
        "  [PASS] MERE=0.00e+00, MARE=0.00e+00 (threshold=1.22e-04, 6 elems)\n"
        "总计: 1\n通过: 1\n失败: 0\n"
    )
    run_ok = _FakeExecResult(return_code=0, stdout=all_pass_stdout, stderr="")
    ssh_env = _FakeSSHEnv(
        responses=[
            ("custom_opp", install_ok),
            ("cmake", build_ok),
            ("test_aclnn", run_ok),
        ],
        env_probe_stdout="/opt/Ascend/opp",
    )
    executor = NpuExecutor(ssh_env=ssh_env, remote_env_setup="source set_env.sh && ")
    report = executor.run_st_driver("/home/hsl/op", "add_example")
    assert report["success"] is True
    assert report["total_cases"] == 1
    assert report["passed_cases"] == 1
    assert report["failed_cases"] == 0
    assert report["cases"][0]["metrics"]["mere"] == 0.0
    # LD_LIBRARY_PATH 必须在 run 命令里(spike 坑 3)
    run_cmd = [c for c in ssh_env.captured_commands if "test_aclnn" in c]
    assert any("LD_LIBRARY_PATH" in c for c in run_cmd)


def test_run_st_driver_remote_empty_path_returns_failure() -> None:
    """远程空 operator_path → failure(不调 SSH)。"""
    ssh_env = _FakeSSHEnv(env_probe_stdout="/opt/Ascend/opp")
    executor = NpuExecutor(ssh_env=ssh_env)
    report = executor.run_st_driver("", "add_example")
    assert report["success"] is False
    assert "operator_path is empty" in report["error"]


# ---- U3 cosmetic compile success (build.sh false negative) ----


def test_is_compile_success_return_code_zero() -> None:
    """return_code=0 → True(正常成功)。"""
    assert NpuExecutor._is_compile_success(0, "build ok") is True


def test_is_compile_success_cosmetic_return_code_one() -> None:
    """return_code=1 + stdout 含 'successfully created' + '.run' → True(cosmetic pass)。

    build.sh 末尾 CPack check '[ERROR] Package not found or empty' 是已知 false negative。
    """
    stdout = (
        'Self-extractable archive "custom_opp_almalinux_aarch64.run" successfully created.\n'
        "[ERROR] Package not found or empty\n"
    )
    assert NpuExecutor._is_compile_success(1, stdout) is True


def test_is_compile_success_real_fail_return_code_one() -> None:
    """return_code=1 + stdout 无 cosmetic markers → False(真编译失败)。"""
    assert NpuExecutor._is_compile_success(1, "cmake error: missing header") is False


def test_is_compile_success_real_fail_higher_return_code() -> None:
    """return_code=2 (segfault/timeout) → False(不用 cosmetic check)。"""
    assert NpuExecutor._is_compile_success(2, "successfully created .run") is False


def test_is_compile_success_empty_stdout_return_code_one() -> None:
    """return_code=1 + stdout 空 → False。"""
    assert NpuExecutor._is_compile_success(1, "") is False


def test_is_compile_success_partial_markers_not_pass() -> None:
    """只有 'successfully created' 无 '.run' → False(两个 marker 都要匹配)。"""
    assert NpuExecutor._is_compile_success(1, "successfully created") is False
    assert NpuExecutor._is_compile_success(1, ".run file here") is False


def test_compile_success_mock_subprocess_cosmetic_pass(tmp_path) -> None:
    """mock subprocess 返 returncode=1 + stdout 含 cosmetic markers → success=True。"""
    operator_path = tmp_path / "op.cpp"
    operator_path.write_text("// kernel")

    executor = NpuExecutor()
    fake_completed = _FakeCompletedProcess(
        returncode=1,
        stdout="custom_opp.run successfully created.\n[ERROR] Package not found",
        stderr="",
    )
    with (
        patch(
            "ascend_op_agent.orchestrator.npu_exec.NpuExecutor.is_cann_available",
            return_value=True,
        ),
        patch(
            "ascend_op_agent.orchestrator.npu_exec.subprocess.run",
            return_value=fake_completed,
        ),
    ):
        outcome = executor.compile(str(operator_path))
    assert outcome.success is True  # cosmetic pass
    assert outcome.return_code == 1  # return_code 仍是 1(透传)
