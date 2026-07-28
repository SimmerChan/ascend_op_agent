"""S0-3 / U13 硬件冒烟测试(A5/950)。

**Spike 0 硬门槛** —— P1-1 解锁后(用户在 A5/950 验证 cann_compile 可用 +
CANN env 配好)自动跑通;本地开发机干净跳过(``conftest.py`` 根据
``ASCEND_OPP_PATH`` / ``CANN_HOME`` 自动 skip)。

覆盖:

- ``NpuExecutor.is_cann_available()`` 在硬件环境返回 True
- ``NpuExecutor.compile`` 对 trivial kernel 返回 success=True + return_code=0
- ``NpuExecutor.compile_to_dict`` 归档 JSON 写盘

fixtures:

- ``tests/fixtures/trivial_kernel/`` —— 一个最简 AscendC kernel(P1-1 后由用户
  补全或从 cannbot-skills 示例复制)

运行::

    pytest -m hardware         # 只跑硬件套件
    pytest tests/              # 默认跳过 hardware
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ascend_op_agent.orchestrator import NpuExecutor


_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "trivial_kernel"


@pytest.mark.hardware
def test_cann_env_configured_on_hardware() -> None:
    """硬件环境必须有 ASCEND_OPP_PATH 或 CANN_HOME。"""
    assert (
        NpuExecutor.is_cann_available()
    ), "ASCEND_OPP_PATH / CANN_HOME 未设置 —— hardware 测试不应在此环境运行"


@pytest.mark.hardware
def test_compile_trivial_kernel_succeeds(tmp_path) -> None:
    """对 trivial kernel 跑 cann_compile,断言 return_code=0 + 产物存在。

    需要 ``tests/fixtures/trivial_kernel/`` 存在(P1-1 后补全)。
    """
    if not _FIXTURE_DIR.is_dir():
        pytest.skip(f"fixture 目录不存在: {_FIXTURE_DIR} (P1-1 后补全 trivial kernel)")

    operator_path = str(_FIXTURE_DIR)
    executor = NpuExecutor(archive_dir=tmp_path)
    outcome = executor.compile(operator_path)

    assert outcome.success is True, f"编译失败: {outcome.stderr}"
    assert outcome.return_code == 0
    # 归档 JSON
    archives = list(tmp_path.glob("compile_*.json"))
    assert len(archives) == 1
    data = json.loads(archives[0].read_text())
    assert data["success"] is True


@pytest.mark.hardware
def test_precision_metrics_on_compiled_artifact(tmp_path) -> None:
    """对编译产物的 golden vs actual 跑 numpy diff(S0-4 协议)。

    P1-1 后:用真实算子的 golden tensor + NPU 实际输出对比。
    本测试只验证 NpuExecutor.run_precision 在硬件上的基本可用性。
    """
    import numpy as np

    executor = NpuExecutor(archive_dir=tmp_path)
    # trivial kernel(如 add)的 golden vs actual —— identical 预期通过
    golden = np.array([1.0, 2.0, 3.0], dtype=np.float16)
    report = executor.run_precision(
        "trivial_add",
        [{"golden": golden, "actual": golden}],
    )
    assert report["total_cases"] == 1
    assert report["passed_cases"] == 1
    assert report["failed_cases"] == 0
