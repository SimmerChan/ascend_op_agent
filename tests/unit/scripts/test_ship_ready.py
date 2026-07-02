"""U8 ship_ready.py 单测(mock subprocess, 不真跑 ruff/pytest/910B)。

覆盖 (P1 plan U8 F-P1-SCOPE-08):
- happy: 4 step 全 PASS → exit 0 + "SHIP READY"
- lint fail → exit 1 + "NOT READY: lint" (立即 abort, 不跑 unit_test)
- unit_test fail → exit 1 + "NOT READY: unit_test"
- stress fail → exit 1 + "NOT READY: stress"
- --skip-stress → stress [SKIP], 其他仍跑
- --skip-e2e → e2e_tui [SKIP]
- e2e_tui placeholder 默认自动跳 (U4 未实施)
- --only unit_test → 只跑 unit_test
- timeout → [FAIL] ... TIMEOUT

不依赖 910B / LLM / ruff / pytest 真实安装。所有 subprocess.run 走 mock。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# 动态加载 scripts/ship_ready.py(非 package)
_SR_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "ship_ready.py"
_spec = importlib.util.spec_from_file_location("ship_ready", _SR_PATH)
assert _spec and _spec.loader, "load ship_ready spec failed"
sr = importlib.util.module_from_spec(_spec)
sys.modules["ship_ready"] = sr
_spec.loader.exec_module(sr)


def _mock_subprocess_returncode(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """构造 mock subprocess.run 返回值。"""
    m = MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


def _run_main(argv: list[str], subprocess_results: list) -> tuple[int, str]:
    """跑 main 并返 (exit_code, stdout)。subprocess_results 是 mock 返回值队列。"""
    with patch.object(sr.subprocess, "run", side_effect=subprocess_results):
        with patch.object(sys, "argv", ["ship_ready.py"] + argv):
            captured_out = []
            with patch(
                "builtins.print", lambda *a, **kw: captured_out.append(" ".join(str(x) for x in a))
            ):
                try:
                    rc = sr.main()
                except SystemExit as e:
                    rc = e.code
            return rc, "\n".join(captured_out)


# ---- happy path ----


def test_happy_all_pass_exit_zero() -> None:
    """4 step 全 PASS → exit 0 + SHIP READY。"""
    results = [
        _mock_subprocess_returncode(0, "lint clean", ""),
        _mock_subprocess_returncode(0, "tests passed", ""),
        _mock_subprocess_returncode(0, "stress 20/20", ""),
        _mock_subprocess_returncode(0, "e2e ok", ""),
    ]
    rc, out = _run_main([], results)
    assert rc == 0
    assert "SHIP READY" in out
    assert "[PASS] lint" in out
    assert "[PASS] unit_test" in out


# ---- 失败路径 ----


def test_lint_fail_aborts_immediately() -> None:
    """lint fail → exit 1 + NOT READY: lint, 不跑 unit_test。"""
    results = [
        _mock_subprocess_returncode(1, "", "E501 line too long"),
    ]
    rc, out = _run_main([], results)
    assert rc == 1
    assert "NOT READY: lint" in out
    assert "[FAIL] lint" in out
    assert "E501" in out  # stderr tail 显示
    # 不应跑 unit_test
    assert "[RUN] unit_test" not in out


def test_unit_test_fail_exit_one() -> None:
    """unit_test fail → exit 1 + NOT READY: unit_test。"""
    results = [
        _mock_subprocess_returncode(0, "lint clean", ""),
        _mock_subprocess_returncode(1, "", "3 failed, 47 passed"),
    ]
    rc, out = _run_main([], results)
    assert rc == 1
    assert "NOT READY: unit_test" in out
    assert "[FAIL] unit_test" in out


def test_stress_fail_exit_one() -> None:
    """stress fail → exit 1 + NOT READY: stress (但 lint+unit_test 已 pass)。"""
    results = [
        _mock_subprocess_returncode(0, "lint clean", ""),
        _mock_subprocess_returncode(0, "tests passed", ""),
        _mock_subprocess_returncode(1, "", "REAL FAIL first-try 0.0% < 80%"),
    ]
    rc, out = _run_main(["--skip-e2e"], results)  # 跳占位 e2e
    assert rc == 1
    assert "NOT READY: stress" in out


# ---- skip flags ----


def test_skip_stress_skips_stress_step() -> None:
    """--skip-stress → stress [SKIP], 不调 subprocess.run for stress。"""
    results = [
        _mock_subprocess_returncode(0, "lint", ""),
        _mock_subprocess_returncode(0, "tests", ""),
    ]
    rc, out = _run_main(["--skip-stress", "--skip-e2e"], results)
    assert rc == 0
    assert "[SKIP] stress" in out
    assert "[SKIP] e2e_tui" in out


def test_e2e_placeholder_auto_skipped() -> None:
    """e2e_tui placeholder (U4 未实施) 默认自动跳,不需 --skip-e2e。"""
    results = [
        _mock_subprocess_returncode(0, "lint", ""),
        _mock_subprocess_returncode(0, "tests", ""),
        _mock_subprocess_returncode(0, "stress", ""),
    ]
    rc, out = _run_main(["--skip-stress"], results)
    # e2e 占位 → 自动 SKIP (即使没 --skip-e2e)
    assert "[SKIP] e2e_tui" in out
    assert "placeholder" in out
    assert rc == 0


# ---- --only ----


def test_only_runs_single_step() -> None:
    """--only unit_test → 只跑 unit_test, 不跑 lint/stress/e2e。"""
    results = [
        _mock_subprocess_returncode(0, "tests passed", ""),
    ]
    rc, out = _run_main(["--only", "unit_test"], results)
    assert rc == 0
    assert "[RUN] unit_test" in out
    assert "[RUN] lint" not in out
    assert "[RUN] stress" not in out


def test_only_lint_fail_exit_one() -> None:
    """--only lint + lint fail → exit 1。"""
    results = [
        _mock_subprocess_returncode(1, "", "lint err"),
    ]
    rc, out = _run_main(["--only", "lint"], results)
    assert rc == 1
    assert "NOT READY: lint" in out


# ---- timeout ----


def test_timeout_returns_fail() -> None:
    """subprocess 抛 TimeoutExpired → [FAIL] ... TIMEOUT。"""

    def _raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=300)

    with patch.object(sr.subprocess, "run", side_effect=_raise_timeout):
        with patch.object(sys, "argv", ["ship_ready.py", "--only", "lint"]):
            captured = []
            with patch(
                "builtins.print", lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
            ):
                try:
                    rc = sr.main()
                except SystemExit as e:
                    rc = e.code
    assert rc == 1
    out = "\n".join(captured)
    assert "[FAIL] lint" in out
    assert "TIMEOUT" in out


# ---- command not found ----


def test_command_not_found_returns_fail() -> None:
    """subprocess 抛 FileNotFoundError → [FAIL] command not found。"""

    def _raise_fnf(*a, **kw):
        raise FileNotFoundError("[Errno 2] No such file: 'ruff'")

    with patch.object(sr.subprocess, "run", side_effect=_raise_fnf):
        with patch.object(sys, "argv", ["ship_ready.py", "--only", "lint"]):
            captured = []
            with patch(
                "builtins.print", lambda *a, **kw: captured.append(" ".join(str(x) for x in a))
            ):
                try:
                    rc = sr.main()
                except SystemExit as e:
                    rc = e.code
    assert rc == 1
    out = "\n".join(captured)
    assert "[FAIL] lint" in out
    assert "command not found" in out
