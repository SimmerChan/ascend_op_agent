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

"""U7 意图分类器 —— LLM 路由用户输入到 4 label(KTD2 hybrid + R5 + KTD13)。

4 label(R5):
  - ``on-task``        :推进当前 active task(继续算子开发 / 跑编译)
  - ``off-task``       :与任务无关的闲聊 / 问候
  - ``new-task``       :要开新任务(``suggested_task_type`` ∈ migrate/analyze/optimize/develop)
  - ``progress-query`` :查进展("现在到哪了 / 进度")

兜底(KTD13):低置信 / LLM 超时 / LLM 不可用 → 默认 on-active-task。**#8 补丁**:
当 active task 为 null 或不可运行(state ∉ {draft, running},即 done/failed/paused)
时,on-active-task 无 target → 改返回 **disambiguation prompt**("无 active task — 当作
新任务还是选一个?")而非 silent spawn。

**DI LLM**:``llm_call: Callable[[str], str]``,None 优雅降级(单测/无 LLM 环境返
fallback)。模式参考 ``relation_builder.py`` 的 DI。

KTD7 三模式(由 ``route(default_mode=...)`` 实现,#7 真实现):
  - ``explicit-only``(默认):free-text 不分类(defer 到显式命令解析)。
  - ``always-on-task``:所有 free-text 当 on-task(跳过 LLM)。
  - ``default-on``:真走分类器(follow-up plan 切,fallback 路径已可用)。
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Callable, Optional

from ascend_op_agent.agent.prompt_builder import build_classifier_prompt
from ascend_op_agent.task_store.models import (
    STATE_DRAFT,
    STATE_RUNNING,
    TASK_TYPES,
)

logger = logging.getLogger(__name__)

# ---- 4 label(R5) ----
LABEL_ON_TASK = "on-task"
LABEL_OFF_TASK = "off-task"
LABEL_NEW_TASK = "new-task"
LABEL_PROGRESS_QUERY = "progress-query"
LABELS = (LABEL_ON_TASK, LABEL_OFF_TASK, LABEL_NEW_TASK, LABEL_PROGRESS_QUERY)

# ---- KTD7 三模式(#7) ----
MODE_EXPLICIT_ONLY = "explicit-only"
MODE_ALWAYS_ON_TASK = "always-on-task"
MODE_DEFAULT_ON = "default-on"
MODES = (MODE_EXPLICIT_ONLY, MODE_ALWAYS_ON_TASK, MODE_DEFAULT_ON)

# 低置信阈值占位(D7:fixture-driven 校准 follow-up;与 RelationBuilder 一致)
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
# LLM 调用超时(KTD13)
DEFAULT_TIMEOUT_S = 5.0
# active task "可运行"状态(rollup.py 推导;done/failed/paused 不可运行)
RUNNABLE_STATES = (STATE_DRAFT, STATE_RUNNING)

# LLM callable 契约:输入 prompt 文本,输出 LLM 响应文本(预期含 JSON)。
LlmCallable = Callable[[str], str]


@dataclass
class ActiveTaskContext:
    """传入分类器的 active task 上下文(决定 #8 fallback 落点)。

    Args:
        task_id: active task id。
        task_type: active task 类型(migrate/analyze/optimize/develop)。
        state: active task 的 R15 rollup 状态(draft/running/done/failed/paused)。
            决定 ``runnable`` —— 仅 draft/running 时 fallback 走 on-active-task。
        object_payload: active task 的 object 描述(供 LLM 判断 on-task 相关性)。
    """

    task_id: str
    task_type: str = ""
    state: str = ""
    object_payload: dict = field(default_factory=dict)

    @property
    def runnable(self) -> bool:
        """active task 是否可执行(#8):state ∈ {draft, running}。"""
        return self.state in RUNNABLE_STATES


@dataclass
class ClassificationResult:
    """分类结果。

    Attributes:
        label: 4 label 之一;``route(explicit-only)`` 时为空串 + ``classified=False``。
        confidence: LLM 置信度(fallback / always-on-task 取占位值)。
        suggested_task_type: new-task 时建议的 task type(R7 拆解);其余 None。
        fallback: 是否走了兜底(KTD13 / #8)。
        fallback_reason: 兜底原因(low-confidence / timeout / llm-unavailable /
            parse-failure / no-active / always-on-task-mode)。
        disambiguate: #8 —— 是否需向用户发 disambiguation prompt(非 silent spawn)。
        disambiguation_prompt: disambiguate=True 时的提示文案。
        classified: False = 模式显式 defer(explicit-only),不应按 label 路由。
    """

    label: str
    confidence: float = 0.0
    suggested_task_type: Optional[str] = None
    fallback: bool = False
    fallback_reason: Optional[str] = None
    disambiguate: bool = False
    disambiguation_prompt: Optional[str] = None
    classified: bool = True


# disambiguation prompt(#8)
_DISAMBIG_PROMPT = (
    "未明确归类且当前无可运行的 active task —— 当作新任务开始,还是用 "
    "`ascend-op-agent task select <id>` 选一个已有任务?"
)


class IntentClassifier:
    """LLM 意图分类器(R5 + KTD13 + #8)。

    Args:
        llm_call: LLM callable(``(prompt: str) -> str``);None 时优雅降级返 fallback。
        confidence_threshold: 低于此置信 → fallback(默认 0.6 占位)。
        timeout_s: LLM 调用超时秒数(KTD13,默认 5s);None / <=0 时不限时。
    """

    def __init__(
        self,
        llm_call: Optional[LlmCallable] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        timeout_s: Optional[float] = DEFAULT_TIMEOUT_S,
    ):
        self.llm_call = llm_call
        self.threshold = confidence_threshold
        self.timeout_s = timeout_s

    # ---- 主分类入口(KTD13 + #8) ----

    def classify(
        self,
        user_input: str,
        active_task_context: Optional[ActiveTaskContext] = None,
    ) -> ClassificationResult:
        """分类 user_input 到 4 label 之一。

        Returns:
            ``ClassificationResult``。LLM 不可用 / 超时 / 低置信 / 解析失败 → fallback
            (KTD13);active task 不可运行 → disambiguation(#8)。**永不抛**(R5 兜底)。
        """
        if self.llm_call is None:
            return self._fallback(active_task_context, reason="llm-unavailable")

        prompt = build_classifier_prompt(user_input, active_task_context)
        try:
            raw = self._call_llm(prompt)
        except _LlmTimeout:
            logger.warning("classifier LLM timeout (>%ss) → fallback", self.timeout_s)
            return self._fallback(active_task_context, reason="timeout")
        except Exception as e:  # noqa: BLE001 - KTD13:任何 LLM 故障不崩,走 fallback
            logger.warning("classifier LLM error → fallback: %s", e)
            return self._fallback(active_task_context, reason="llm-unavailable")

        parsed = self._parse(raw)
        if parsed is None:
            return self._fallback(active_task_context, reason="parse-failure")

        label, confidence, suggested = parsed
        if confidence < self.threshold:
            return self._fallback(
                active_task_context,
                reason="low-confidence",
                llm_label=label,
                llm_confidence=confidence,
            )
        return ClassificationResult(
            label=label,
            confidence=confidence,
            suggested_task_type=suggested,
        )

    # ---- KTD7 三模式路由(#7 真实现) ----

    def route(
        self,
        default_mode: str,
        user_input: str,
        active_task_context: Optional[ActiveTaskContext] = None,
    ) -> ClassificationResult:
        """按 KTD7 模式决定是否分类 + 如何分类。

        - ``explicit-only``:free-text 不分类(``classified=False``),交显式命令解析。
        - ``always-on-task``:所有 free-text 当 on-task(跳过 LLM,#7 约 5 行)。
        - ``default-on``:真走 ``classify``(LLM + fallback)。

        Raises:
            ValueError: default_mode 非法。
        """
        if default_mode == MODE_EXPLICIT_ONLY:
            # KTD7:free-text 不分类,仅显式命令生效
            return ClassificationResult(label="", confidence=0.0, classified=False)
        if default_mode == MODE_ALWAYS_ON_TASK:
            # #7:跳过分类,所有 free-text 当 on-task
            return ClassificationResult(
                label=LABEL_ON_TASK,
                confidence=1.0,
                classified=True,
                fallback_reason="always-on-task-mode",
            )
        if default_mode == MODE_DEFAULT_ON:
            return self.classify(user_input, active_task_context)
        raise ValueError(
            f"unknown default_mode: {default_mode}; expected one of {MODES}"
        )

    # ---- fallback(KTD13 + #8) ----

    def _fallback(
        self,
        active_task_context: Optional[ActiveTaskContext],
        reason: str,
        llm_label: Optional[str] = None,
        llm_confidence: Optional[float] = None,
    ) -> ClassificationResult:
        """KTD13 默认 on-active-task;#8 active 不可运行 → disambiguation。"""
        base = ClassificationResult(
            label=LABEL_ON_TASK,  # KTD13 lean
            confidence=0.0,
            fallback=True,
            fallback_reason=reason,
        )
        if llm_label is not None:
            base.fallback_reason = f"{reason}(llm={llm_label}@{llm_confidence:.2f})"
        # #8:active null / 不可运行 → 无 target,改 disambiguation
        if active_task_context is None or not active_task_context.runnable:
            base.disambiguate = True
            base.disambiguation_prompt = _DISAMBIG_PROMPT
            base.label = LABEL_NEW_TASK  # lean:引导开新 / 选已有,不 silent spawn
        return base

    # ---- LLM 调用(可注入 timeout,KTD13) ----

    def _call_llm(self, prompt: str) -> str:
        if self.llm_call is None:
            raise _LlmUnavailable()
        if self.timeout_s is None or self.timeout_s <= 0:
            try:
                return self.llm_call(prompt)
            except TimeoutError as e:  # callable 自报超时(LLM client timeout)
                raise _LlmTimeout() from e
        # 真超时兜底:线程池 + future.result(timeout)。超时无法杀线程,但分类已走
        # fallback(单测可用 mock raise TimeoutError 或 sleep + 小 timeout_s 实测)。
        # 注:py3.10 上 concurrent.futures.TimeoutError 与 builtins.TimeoutError 是不同
        # 类(3.11 才合一),两者都视作 timeout。
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(self.llm_call, prompt)
            try:
                return fut.result(timeout=self.timeout_s)
            except (FuturesTimeoutError, TimeoutError) as e:
                raise _LlmTimeout() from e

    # ---- 响应解析(健壮:容 markdown / 非法 JSON / 字段缺失) ----

    @staticmethod
    def _parse(raw: str) -> Optional[tuple]:
        """解析 LLM 响应 → ``(label, confidence, suggested_task_type)``;失败返 None。"""
        if not raw:
            return None
        match = re.search(r"\{[\s\S]*\}", raw)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        label = data.get("label")
        if label not in LABELS:
            return None
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        suggested = data.get("suggested_task_type")
        if suggested not in TASK_TYPES:
            suggested = None
        return (label, conf, suggested)


class _LlmTimeout(Exception):
    """LLM 调用超过 timeout_s(KTD13,内部信号,不外抛)。"""


class _LlmUnavailable(Exception):
    """LLM 未 wiring(内部信号,不外抛)。"""
