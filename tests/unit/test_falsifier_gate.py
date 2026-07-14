# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""R16 gate 自动判定:falsifier_baseline.build_report metric 汇总 + GO/NO-GO 矩阵。

gate:GO = switches>=threshold OR complaints>0;NO-GO(唯一) = switches<threshold
AND complaints==0。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# scripts/ 不是包,显式加入 sys.path 以 import build_report
_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from falsifier_baseline import build_report  # noqa: E402

from ascend_op_agent.task_store import TASK_TYPE_DEVELOP, TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def _seed_switches(store, n):
    for i in range(n):
        store.record_metric("spontaneous_task_switch", from_task=f"f{i}", to_task=f"t{i}")


def _seed_complaints(store, n):
    for i in range(n):
        store.record_metric("context_juggling_complaint", detail={"note": f"c{i}"})


def test_gate_no_go_when_both_below(store):
    """switches<threshold AND complaints==0 → NO-GO(唯一 NO-GO 路径)。"""
    _seed_switches(store, 4)  # < 5
    r = build_report(store, threshold=5)
    assert r["go_no_go"]["decision"].startswith("NO-GO")
    assert r["dogfood_metrics"]["spontaneous_task_switches"] == 4
    assert r["dogfood_metrics"]["context_juggling_complaints"] == 0


def test_gate_go_when_switches_at_threshold(store):
    _seed_switches(store, 5)
    assert build_report(store, threshold=5)["go_no_go"]["decision"] == "GO"


def test_gate_go_when_switches_above(store):
    _seed_switches(store, 10)
    assert build_report(store, threshold=5)["go_no_go"]["decision"] == "GO"


def test_gate_go_when_complaints_present_even_if_switches_low(store):
    """switches<threshold 但 complaints>0 → GO(痛点真实)。"""
    _seed_switches(store, 1)
    _seed_complaints(store, 1)
    r = build_report(store, threshold=5)
    assert r["go_no_go"]["decision"] == "GO"
    assert r["dogfood_metrics"]["context_juggling_complaints"] == 1


def test_gate_threshold_configurable(store):
    """threshold 可配:switches=2,threshold=2 → GO;threshold=5 → NO-GO。"""
    _seed_switches(store, 2)
    assert build_report(store, threshold=2)["go_no_go"]["decision"] == "GO"
    assert build_report(store, threshold=5)["go_no_go"]["decision"].startswith("NO-GO")


def test_report_keeps_task_state_fields(store):
    """falsifier 输出向后兼容:task_count/active/tasks 保留。"""
    store.create_task(TASK_TYPE_DEVELOP)
    r = build_report(store)
    assert r["task_count"] == 1
    assert "active_task_id" in r
    assert len(r["tasks"]) == 1
    assert r["tasks"][0]["type"] == TASK_TYPE_DEVELOP


def test_since_filter_excludes_future_metrics(store):
    """since 过滤:只计 created_at >= since 的 metric。"""
    _seed_switches(store, 3)
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400))
    r = build_report(store, since=future, threshold=5)
    assert r["dogfood_metrics"]["spontaneous_task_switches"] == 0
    assert r["go_no_go"]["decision"].startswith("NO-GO")
