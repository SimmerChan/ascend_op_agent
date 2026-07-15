# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""C 方案:complaints 半自动采集 — detect_churn(窗口阈值 + 冷却)+ 主动询问 单测。

detect_churn 是 complaints 的**行为代理信号(仅提醒询问,不进 gate)**:最近 window 分钟内
spontaneous_task_switch >= threshold 且不在冷却期 → 触发 CLI 询问。complaint 是否记仍由
用户回答(click.confirm)决定,保留主观判定准确性(避免"从容多任务"被行为信号误判)。
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from ascend_op_agent.task_router import commands as cmd_mod
from ascend_op_agent.task_router.commands import TaskCommands
from ascend_op_agent.task_store import TaskStore


@pytest.fixture
def cmds(tmp_path):
    return TaskCommands(TaskStore(tmp_path / "tasks.db"))


def _seed_switches(cmds, n):
    """造 n 条 spontaneous_task_switch(直接 record,都在当前窗口内)。"""
    for i in range(n):
        cmds.store.record_metric("spontaneous_task_switch", from_task=f"f{i}", to_task=f"t{i}")


# ---- store get_meta/set_meta(冷却时间戳存储原语) ----


def test_get_set_meta_roundtrip(cmds):
    cmds.store.set_meta("k1", "v1")
    assert cmds.store.get_meta("k1") == "v1"


def test_get_meta_missing_returns_default(cmds):
    assert cmds.store.get_meta("nope") is None
    assert cmds.store.get_meta("nope", "fallback") == "fallback"


def test_set_meta_upsert_overwrites(cmds):
    cmds.store.set_meta("k", "v1")
    cmds.store.set_meta("k", "v2")
    assert cmds.store.get_meta("k") == "v2"


# ---- detect_churn:窗口阈值 + 冷却 ----


def test_detect_churn_below_threshold_false(cmds):
    _seed_switches(cmds, 2)  # < 3
    assert cmds.detect_churn() is False


def test_detect_churn_at_threshold_true(cmds):
    _seed_switches(cmds, 3)
    assert cmds.detect_churn() is True


def test_detect_churn_above_threshold_true(cmds):
    _seed_switches(cmds, 5)
    assert cmds.detect_churn() is True


def test_detect_churn_threshold_configurable(cmds):
    _seed_switches(cmds, 2)
    assert cmds.detect_churn(threshold=2) is True
    assert cmds.detect_churn(threshold=5) is False


def test_detect_churn_cooldown_blocks(cmds):
    """mark_complaint_prompted 后冷却期内不再触发(防打扰)。"""
    _seed_switches(cmds, 5)
    assert cmds.detect_churn() is True
    cmds.mark_complaint_prompted()
    assert cmds.detect_churn() is False


def test_detect_churn_fires_after_cooldown(cmds, monkeypatch):
    """冷却过期后重新触发(monkeypatch cooldown=0 模拟立即过期)。"""
    monkeypatch.setattr(cmd_mod, "COMPLAINT_COOLDOWN_MINUTES", 0)
    _seed_switches(cmds, 5)
    cmds.mark_complaint_prompted()
    assert cmds.detect_churn() is True


def test_mark_complaint_prompted_writes_meta(cmds):
    cmds.mark_complaint_prompted()
    assert cmds.store.get_meta(cmd_mod.META_LAST_COMPLAINT_PROMPT) is not None


# ---- CLI:非 tty(CliRunner)触发 churn 时打印提示不 hang ----


def test_task_select_churn_prompt_non_tty(tmp_path):
    """触发 churn 时非 tty 打印提示,不阻塞,不自动记 complaint(需 y)。"""
    from ascend_op_agent.cli import main

    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    # 预埋:2 task 来回 select → 3 个 spontaneous switch(窗口内);直调 commands 不经 CLI 询问
    store = TaskStore(db)
    pre = TaskCommands(store)
    a = pre.new("develop")
    b = pre.new("analyze")
    pre.select(a)  # b→a switch1
    pre.select(b)  # a→b switch2
    pre.select(a)  # b→a switch3
    # CLI select b:detect 见窗口内 ≥3 switch → 触发;CliRunner stdin 非 tty → 打印提示
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["task", "--db", str(db), "--ck", str(ck), "select", b],
        input="\n",  # 保险:若误入 confirm 分支,回车=default 不 hang
    )
    assert result.exit_code == 0
    assert "频繁切换" in result.output  # 提示文本两分支(confirm / 非 tty)都含
    # 非 tty 不自动记 complaint(需用户 y)
    assert len(store.list_metrics("context_juggling_complaint")) == 0
