# Copyright 2026 SimperChan
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

"""U7 NudgeResponder 单测(R6 off/soft/firm)。"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_router.intent_classifier import (
    LABEL_OFF_TASK,
    LABEL_ON_TASK,
    ActiveTaskContext,
    ClassificationResult,
)
from ascend_op_agent.task_router.nudge import (
    NUDGE_FIRM,
    NUDGE_OFF,
    NUDGE_SOFT,
    NudgeResponder,
)


def _result(label):
    return ClassificationResult(label=label, confidence=0.9)


def _ctx(task_type="migrate"):
    return ActiveTaskContext(task_id="t1", task_type=task_type, state="running")


def test_off_task_soft_adds_nudge():
    """R6 默认 soft:off-task → 简答 + 软牵引提示。"""
    resp = NudgeResponder()  # default soft
    out = resp.respond(_result(LABEL_OFF_TASK), base_reply="你好呀", active_task_context=_ctx())
    assert out.nudge_applied
    assert out.mode == NUDGE_SOFT
    assert "你好呀" in out.text
    assert "active task" in out.text
    assert "migrate" in out.text  # 引用 active task type


def test_off_task_firm_blocks_back_to_active():
    """R6 firm:off-task → 简答 + 阻断回 active task。"""
    resp = NudgeResponder(mode=NUDGE_FIRM)
    out = resp.respond(_result(LABEL_OFF_TASK), base_reply="你好", active_task_context=_ctx())
    assert out.nudge_applied
    assert out.mode == NUDGE_FIRM
    assert "/task progress" in out.text  # firm 阻断回任务


def test_off_task_off_mode_no_nudge():
    """R6 off:不牵引,只回简答。"""
    resp = NudgeResponder(mode=NUDGE_OFF)
    out = resp.respond(_result(LABEL_OFF_TASK), base_reply="你好")
    assert not out.nudge_applied
    assert out.text == "你好"


def test_on_task_not_nudged():
    """非 off-task(on-task)→ 不牵引,透传 base reply。"""
    for mode in (NUDGE_OFF, NUDGE_SOFT, NUDGE_FIRM):
        resp = NudgeResponder(mode=mode)
        out = resp.respond(_result(LABEL_ON_TASK), base_reply="编译完成")
        assert not out.nudge_applied
        assert out.text == "编译完成"


def test_soft_nudge_no_active_ctx_still_nudges():
    """无 active_task_context 时 soft 仍牵引(文案不引用 type)。"""
    resp = NudgeResponder(mode=NUDGE_SOFT)
    out = resp.respond(_result(LABEL_OFF_TASK), base_reply="hi", active_task_context=None)
    assert out.nudge_applied
    assert "hi" in out.text
    assert "active task" in out.text


def test_empty_base_reply_soft():
    """无简答时 soft 只出牵引文案。"""
    resp = NudgeResponder(mode=NUDGE_SOFT)
    out = resp.respond(_result(LABEL_OFF_TASK), base_reply="", active_task_context=_ctx())
    assert out.nudge_applied
    assert out.text.strip()  # 非空
    assert "active task" in out.text


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="unknown nudge mode"):
        NudgeResponder(mode="bogus")
