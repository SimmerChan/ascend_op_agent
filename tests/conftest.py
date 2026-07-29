"""pytest 全局配置。

- 注册 ``hardware`` marker(真实 CANN / A5-950 硬件测试)
- ``ASCEND_OPP_PATH`` / ``CANN_HOME`` 未设时,hardware 测试自动跳过
  (S0-3 / U1 / U2 等硬件依赖测试在 CI / 开发机干净跳过)
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "hardware: requires real CANN/A5-950 hardware "
        "(skipped if ASCEND_OPP_PATH / CANN_HOME not set)",
    )


def pytest_collection_modifyitems(config, items):
    """CANN 环境未配置时,hardware 测试自动 skip。"""
    cann_ready = bool(os.environ.get("ASCEND_OPP_PATH") or os.environ.get("CANN_HOME"))
    if cann_ready:
        return
    skip_hardware = pytest.mark.skip(
        reason="CANN env not configured (ASCEND_OPP_PATH / CANN_HOME missing)"
    )
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hardware)
