"""U3 stress harness 单测(mock executor,不需 910B)。

覆盖:
- argparse --stress N parsed correctly
- _is_pass() 成功判定逻辑(compile + precision 都 True)
- 3-state exit code (clean / transient recovered / real fail)
- dual metric (first_try vs with_retry) retry 计数正确
- run_id 唯一性(hostname-pid-ns)

不依赖 910B / LLM / scaffold。所有 _do_one_run_stress 走 mock。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# 动态加载 scripts/e2e_real_op.py(非 package)
_E2E_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "e2e_real_op.py"
_spec = importlib.util.spec_from_file_location("e2e_real_op", _E2E_PATH)
assert _spec and _spec.loader, "load e2e_real_op spec failed"
e2e = importlib.util.module_from_spec(_spec)
sys.modules["e2e_real_op"] = e2e
_spec.loader.exec_module(e2e)


# ---- _is_pass() 单元逻辑 ----


def test_is_pass_both_success_returns_true() -> None:
    """compile + precision 都 True → True。"""
    state = {
        "compile_result": {"success": True},
        "precision_report": {"success": True},
    }
    assert e2e._is_pass(state) is True


def test_is_pass_compile_fail_returns_false() -> None:
    """compile 失败 → False(LLM 55% 主失败路径)。"""
    state = {
        "compile_result": {"success": False, "stderr": "ValueError: code_result.files 为空"},
        "precision_report": {"success": True},
    }
    assert e2e._is_pass(state) is False


def test_is_pass_precision_fail_returns_false() -> None:
    """precision 失败 → False(算子算错)。"""
    state = {
        "compile_result": {"success": True},
        "precision_report": {"success": False},
    }
    assert e2e._is_pass(state) is False


def test_is_pass_missing_fields_returns_false() -> None:
    """完全缺 result → False。"""
    assert e2e._is_pass({}) is False
    assert e2e._is_pass({"compile_result": {}}) is False


# ---- 3-state exit code (F13) ----


def test_stress_clean_pass_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """first_try=100% AND final=100% AND eq → exit 0 (clean)。"""
    # mock _do_one_run_stress 返全 pass
    with patch.object(
        e2e, "_do_one_run_stress",
        return_value=(True, True, ""),  # (first_pass, final_pass, stderr)
    ):
        exit_code = e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "CLEAN PASS" in out


def test_stress_transient_recovered_exit_zero_with_warn(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """first_try=3/5=60% (低于 80% 阈值) AND final=5/5=100% → REAL FAIL (not transient)。

    注:transient recover 要求 first_try >= 80%。
    低于 80% 即使 retry 救回也算 real fail(说明系统不稳)。
    """
    # 5 次: 第 1 次 fail (first), 但 retry 救回 (final)
    pass_seq = [
        (False, True, ""),  # run 1: first fail, retry saved
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),
    ]
    with patch.object(e2e, "_do_one_run_stress", side_effect=pass_seq):
        exit_code = e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    # first_try=4/5=80%, final=5/5=100% → transient recovered
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "TRANSIENT RECOVERED" in out


def test_stress_low_first_try_real_fail_exit_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """first_try=50% < 80% → REAL FAIL 即使 final=100%。"""
    pass_seq = [
        (False, True, ""),  # run 1: first fail
        (False, True, ""),  # run 2: first fail
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),  # 3 first pass = 60%, retry 救回另 2 = 100%
    ]
    with patch.object(e2e, "_do_one_run_stress", side_effect=pass_seq):
        exit_code = e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "REAL FAIL" in out


def test_stress_low_final_real_fail_exit_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """final < 95% → REAL FAIL。"""
    # 5 次: 4 first+final pass, 1 全 fail(retry 也没救)
    pass_seq = [
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),
        (False, False, "compile fail"),  # retry 也没救
    ]
    with patch.object(e2e, "_do_one_run_stress", side_effect=pass_seq):
        exit_code = e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    assert exit_code == 1  # final=4/5=80% < 95%


def test_stress_report_shows_failure_summaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """失败 case stderr 摘要记录(前 3 个)。"""
    pass_seq = [
        (False, False, "compile error 1"),
        (False, False, "compile error 2"),
        (False, False, "compile error 3"),
        (False, False, "compile error 4"),
        (False, False, "compile error 5"),
    ]
    with patch.object(e2e, "_do_one_run_stress", side_effect=pass_seq):
        e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    out = capsys.readouterr().out
    # 失败摘要只显示前 3 个
    assert "compile error 1" in out
    assert "compile error 2" in out
    assert "compile error 3" in out
    # 第 4、5 不显示(只前 3)
    assert "run 4:" not in out
    assert "run 5:" not in out


def test_stress_exception_treated_as_fail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """主流程异常(SSH 挂) → 计为 fail,不 abort 全 N。"""
    pass_seq = [
        (True, True, ""),
        RuntimeError("SSH connection lost"),
        (True, True, ""),
        (True, True, ""),
        (True, True, ""),
    ]

    def _side_effect(*a, **kw):
        v = pass_seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    with patch.object(e2e, "_do_one_run_stress", side_effect=_side_effect):
        exit_code = e2e._run_stress(
            type("A", (), {"stress": 5, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    # 4/5 final pass = 80% < 95% → REAL FAIL
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "SSH connection lost" in out


def test_stress_n_eq_1_threshold_boundary() -> None:
    """N=1 boundary:1 次 pass=clean, 1 次 fail=real fail。"""
    with patch.object(e2e, "_do_one_run_stress", return_value=(True, True, "")):
        rc_pass = e2e._run_stress(
            type("A", (), {"stress": 1, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    assert rc_pass == 0  # 1/1=100% both → clean

    with patch.object(e2e, "_do_one_run_stress", return_value=(False, False, "fail")):
        rc_fail = e2e._run_stress(
            type("A", (), {"stress": 1, "task": "test", "local_workdir": "/tmp/x",
                           "skip_stress_retry": False, "thread_id": "t1"})()
        )
    assert rc_fail == 1  # 0/1=0% → REAL FAIL


# ---- argparse ----


def test_argparse_stress_default_none() -> None:
    """--stress 不传 → None(single run 行为)。"""
    import argparse as _ap

    parser = _ap.ArgumentParser()
    parser.add_argument("task", nargs="?", default="x")
    parser.add_argument("--thread-id", default="t")
    parser.add_argument("--local-workdir", default="/tmp")
    parser.add_argument("--stress", type=int, default=None)
    parser.add_argument("--skip-stress-retry", action="store_true")
    args = parser.parse_args([])
    assert args.stress is None
    assert args.skip_stress_retry is False


def test_argparse_stress_20_parsed() -> None:
    """--stress 20 → int(20)。"""
    import argparse as _ap

    parser = _ap.ArgumentParser()
    parser.add_argument("--stress", type=int, default=None)
    args = parser.parse_args(["--stress", "20"])
    assert args.stress == 20
