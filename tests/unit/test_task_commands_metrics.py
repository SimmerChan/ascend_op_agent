# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""R16 dogfood 埋点:TaskCommands select 记 spontaneous switch + flag_complaint 单测。

语义(DOGFOOD_GUIDE 场景 A):只 select 从另一 active 切走时计 spontaneous_task_switch;
new 不经此路径不计;首次选(prev=None)/重选当前(prev==to)不计。
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_router.commands import TaskCommands
from ascend_op_agent.task_store import TASK_TYPE_ANALYZE, TASK_TYPE_DEVELOP, TaskStore


@pytest.fixture
def cmds(tmp_path):
    return TaskCommands(TaskStore(tmp_path / "tasks.db"))


def test_select_from_other_active_records_switch(cmds):
    a = cmds.new(TASK_TYPE_DEVELOP)  # active=a(new 不计)
    b = cmds.new(TASK_TYPE_ANALYZE)  # active=b(new 不计)
    cmds.select(a)  # prev=b → a 计 1 次
    sw = cmds.store.list_metrics("spontaneous_task_switch")
    assert len(sw) == 1
    assert sw[0]["from_task"] == b
    assert sw[0]["to_task"] == a


def test_select_first_time_no_switch(cmds):
    """首次 select(prev=None)不计。"""
    a = cmds.new(TASK_TYPE_DEVELOP)
    cmds.store.clear_active()  # prev → None
    cmds.select(a)
    assert len(cmds.store.list_metrics("spontaneous_task_switch")) == 0


def test_select_same_active_no_switch(cmds):
    """重选当前 active(prev==to)不计。"""
    a = cmds.new(TASK_TYPE_DEVELOP)  # active=a
    cmds.select(a)  # prev=a, to=a
    assert len(cmds.store.list_metrics("spontaneous_task_switch")) == 0


def test_new_does_not_record_spontaneous_switch(cmds):
    """new() 不计 spontaneous switch(忠于 guide 场景 C:new 没标 +1)。"""
    cmds.new(TASK_TYPE_DEVELOP)
    cmds.new(TASK_TYPE_ANALYZE)  # active 切换但 new 不计
    assert len(cmds.store.list_metrics("spontaneous_task_switch")) == 0


def test_multiple_switches_accumulate(cmds):
    a = cmds.new(TASK_TYPE_DEVELOP)
    b = cmds.new(TASK_TYPE_ANALYZE)
    cmds.select(a)  # b→a
    cmds.select(b)  # a→b
    cmds.select(a)  # b→a
    assert len(cmds.store.list_metrics("spontaneous_task_switch")) == 3


def test_flag_complaint_records_with_detail(cmds):
    mid = cmds.flag_complaint("忘了在哪个任务")
    rows = cmds.store.list_metrics("context_juggling_complaint")
    assert len(rows) == 1
    assert rows[0]["id"] == mid
    assert rows[0]["detail"] == {"note": "忘了在哪个任务"}


def test_flag_complaint_empty_detail(cmds):
    cmds.flag_complaint()
    rows = cmds.store.list_metrics("context_juggling_complaint")
    assert len(rows) == 1
    assert rows[0]["detail"] == {}
