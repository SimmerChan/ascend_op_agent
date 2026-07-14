# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""R16 dogfood 埋点:TaskStore task_metrics 表 + record_metric/list_metrics 单测。"""

from __future__ import annotations

import time

import pytest

from ascend_op_agent.task_store import TaskStore


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_record_metric_returns_id_and_persists(store):
    mid = store.record_metric("spontaneous_task_switch", from_task="aaa", to_task="bbb")
    assert isinstance(mid, str) and len(mid) > 0
    rows = store.list_metrics("spontaneous_task_switch")
    assert len(rows) == 1
    assert rows[0]["id"] == mid
    assert rows[0]["from_task"] == "aaa"
    assert rows[0]["to_task"] == "bbb"
    assert rows[0]["metric_name"] == "spontaneous_task_switch"
    assert rows[0]["created_at"] != ""


def test_record_metric_default_detail_empty_dict(store):
    store.record_metric("context_juggling_complaint")
    rows = store.list_metrics("context_juggling_complaint")
    assert rows[0]["detail"] == {}
    assert rows[0]["from_task"] is None
    assert rows[0]["to_task"] is None


def test_record_metric_detail_json_roundtrip(store):
    store.record_metric("context_juggling_complaint", detail={"note": "忘了在哪个任务"})
    rows = store.list_metrics("context_juggling_complaint")
    assert rows[0]["detail"] == {"note": "忘了在哪个任务"}


def test_list_metrics_filters_by_name(store):
    store.record_metric("spontaneous_task_switch", from_task="a", to_task="b")
    store.record_metric("context_juggling_complaint")
    store.record_metric("spontaneous_task_switch", from_task="b", to_task="c")
    assert len(store.list_metrics("spontaneous_task_switch")) == 2
    assert len(store.list_metrics("context_juggling_complaint")) == 1


def test_list_metrics_all_when_no_filter(store):
    store.record_metric("spontaneous_task_switch")
    store.record_metric("context_juggling_complaint")
    assert len(store.list_metrics()) == 2


def test_list_metrics_since_filter(store):
    store.record_metric("spontaneous_task_switch")
    # since 设未来 → 过滤掉全部
    future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400))
    assert len(store.list_metrics(since=future)) == 0
    # since 设过去 → 全部
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    assert len(store.list_metrics(since=past)) == 1


def test_list_metrics_returns_all_inserted(store):
    store.record_metric("spontaneous_task_switch", to_task="first")
    store.record_metric("spontaneous_task_switch", to_task="second")
    rows = store.list_metrics("spontaneous_task_switch")
    assert {r["to_task"] for r in rows} == {"first", "second"}


def test_metric_table_created_idempotent(tmp_path):
    """_init_schema 幂等:新实例同 db 不重建 task_metrics,旧 metric 保留。"""
    db = tmp_path / "tasks.db"
    s1 = TaskStore(db)
    s1.record_metric("spontaneous_task_switch")
    s2 = TaskStore(db)  # 再跑 _init_schema(CREATE TABLE IF NOT EXISTS 幂等)
    assert len(s2.list_metrics("spontaneous_task_switch")) == 1
