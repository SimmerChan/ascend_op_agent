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

"""U7 nudge —— off-task 软牵引(R6,off/soft/firm 三模式,默认 soft)。

仅对 ``off-task`` label 生效;其余 label 透传 base reply 不牵引。

- ``off``  :不牵引,只回简答(off-task 当普通闲聊回)。
- ``soft`` :简答 + 软牵引提示("当前有 active task,需要可继续")。默认。
- ``firm`` :简答 + 阻断回 active task("建议先回任务,/task progress 看进度")。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ascend_op_agent.task_router.intent_classifier import (
    LABEL_OFF_TASK,
    ActiveTaskContext,
    ClassificationResult,
)

NUDGE_OFF = "off"
NUDGE_SOFT = "soft"
NUDGE_FIRM = "firm"
NUDGE_MODES = (NUDGE_OFF, NUDGE_SOFT, NUDGE_FIRM)


@dataclass
class NudgeResult:
    """nudge 响应包装。

    Attributes:
        text: 实际展示给用户的文本(base reply + 可选牵引)。
        nudge_applied: 是否加了牵引提示(仅 off-task + soft/firm)。
        mode: 当前 nudge 模式。
    """

    text: str
    nudge_applied: bool
    mode: str


class NudgeResponder:
    """off-task 软牵引响应器(R6)。

    Args:
        mode: nudge 模式(off/soft/firm),默认 ``soft``。
    """

    def __init__(self, mode: str = NUDGE_SOFT):
        if mode not in NUDGE_MODES:
            raise ValueError(f"unknown nudge mode: {mode}; expected one of {NUDGE_MODES}")
        self.mode = mode

    def respond(
        self,
        classification: ClassificationResult,
        base_reply: str = "",
        active_task_context: Optional[ActiveTaskContext] = None,
    ) -> NudgeResult:
        """按模式产出最终展示文本。

        Args:
            classification: 分类结果(仅 ``off-task`` 触发牵引)。
            base_reply: off-task 的简答内容(如闲聊回复)。
            active_task_context: 当前 active task(牵引文案引用其 type)。

        Returns:
            ``NudgeResult`` —— 非 off-task / mode=off → 透传 base_reply 不牵引。
        """
        # 仅 off-task 牵引;其余 label 不动 caller 的执行流
        if classification.label != LABEL_OFF_TASK or self.mode == NUDGE_OFF:
            return NudgeResult(text=base_reply, nudge_applied=False, mode=self.mode)

        nudge = self._nudge_text(active_task_context)
        text = f"{base_reply}\n\n{nudge}" if base_reply else nudge
        return NudgeResult(text=text, nudge_applied=True, mode=self.mode)

    def _nudge_text(self, active_task_context: Optional[ActiveTaskContext]) -> str:
        ctx_desc = ""
        if active_task_context is not None:
            t = active_task_context.task_type or "任务"
            ctx_desc = f"({t}) "
        if self.mode == NUDGE_SOFT:
            return (
                f"（提醒:当前有 active task {ctx_desc}进行中,需要的话可以继续推进哦。）"
            )
        # firm
        return (
            f"（当前有 active task {ctx_desc}进行中,建议先回到任务:"
            "用 `/task progress` 查看进度,或继续上一步操作。）"
        )
