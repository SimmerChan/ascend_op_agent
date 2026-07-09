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

"""U5 relation_builder —— LLM 推荐关系(KTD4 suggest + confirm,非 silent)。

读新 task 的 ``object_payload`` + active task context,LLM 推荐 spawned-by /
depends-on 边。**仅 suggest,不落库**(KTD4);用户 ``/task link`` 确认才由
TaskCommands.link → RelationStore.add_relation 落库。低置信(< threshold)不
recommend(避免噪声;threshold 默认 0.6 占位,真实校准在 U7 follow-up)。

LLM 接入用 **dependency injection**:``llm_call: Callable[[str], str]`` —— 单测注入
mock,避免依赖真 LLM;CLI 运行时按需 wiring(见 TaskCommands.suggest)。

模式参考:cannbot_loader 的 description-triggered routing(LLM 读 context 推荐)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ascend_op_agent.task_store import TaskStore
from ascend_op_agent.task_store.relations import (
    RELATION_DEPENDS_ON,
    RELATION_SPAWNED_BY,
    RELATION_TYPES,
)

# 低置信不 recommend —— 默认 0.6 占位(D7:fixture-driven 校准 follow-up U7)
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# LLM callable 契约:输入 prompt 文本,输出 LLM 响应文本(预期含 JSON)。
LlmCallable = Callable[[str], str]


@dataclass
class RelationSuggestion:
    """一条未落库的关系建议(src→dst,含 type/confidence/rationale)。"""

    src_task_id: str
    dst_task_id: str
    relation_type: str
    confidence: float
    rationale: str = ""


class RelationBuilder:
    """LLM 推荐关系(KTD4 suggest + confirm)。

    Args:
        store: TaskStore(读 task object_payload / active task)。
        llm_call: LLM 调用 callable(``(prompt: str) -> str``);None 时 suggest
            返回空(不接 LLM —— CLI 未 wiring LLM 时优雅降级,单测可注入 mock)。
        confidence_threshold: 低于此置信的 suggest 不返回(默认 0.6 占位)。
    """

    def __init__(
        self,
        store: TaskStore,
        llm_call: Optional[LlmCallable] = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self.store = store
        self.llm_call = llm_call
        self.threshold = confidence_threshold

    def suggest(
        self,
        new_task_id: str,
        active_task_id: Optional[str] = None,
    ) -> List[RelationSuggestion]:
        """对新 task 推荐关系(指向 active task)。**不落库**。

        Args:
            new_task_id: 新建/被分析的 task(作为关系 src —— spawned-by / depends-on
                active task)。
            active_task_id: 参照 task;缺省取 store active。None / 等于 new_task_id
                / 不存在 → 返回空(无参照不推荐)。

        Returns:
            过滤 threshold 后的建议列表(不落库)。无 LLM / 无参照 → 空。

        Raises:
            KeyError: new_task_id 不存在。
        """
        new_task = self.store.get_task(new_task_id)
        if new_task is None:
            raise KeyError(f"unknown task: {new_task_id}")

        active_id = active_task_id or self.store.get_active()
        if active_id is None or active_id == new_task_id:
            return []  # 无参照或自指 → 不推荐
        active_task = self.store.get_task(active_id)
        if active_task is None:
            return []

        if self.llm_call is None:
            return []  # 未 wiring LLM —— 优雅降级(单测/无 LLM 环境)

        prompt = self._build_prompt(new_task, active_id, active_task)
        raw = self.llm_call(prompt)
        candidates = self._parse(raw, src_task_id=new_task_id, dst_task_id=active_id)
        return [c for c in candidates if c.confidence >= self.threshold]

    # ---- prompt 组装 + 解析 ----

    def _build_prompt(self, new_task: Any, active_id: str, active_task: Any) -> str:
        """组装 LLM prompt(读两 task 的 type + object_payload)。"""
        return (
            "你是任务关系分析器。判断新任务与参照任务之间的关系,只输出 JSON。\n\n"
            f"新任务(id={new_task.id}, type={new_task.type}):\n"
            f"{json.dumps(new_task.object_payload, ensure_ascii=False)}\n\n"
            f"参照任务(id={active_id}, type={active_task.type}):\n"
            f"{json.dumps(active_task.object_payload, ensure_ascii=False)}\n\n"
            "关系类型:\n"
            f"- {RELATION_SPAWNED_BY}:新任务由参照任务派生(如迁移中 spawn analyze)\n"
            f"- {RELATION_DEPENDS_ON}:新任务依赖参照任务的产物\n\n"
            "无关系则返回空数组。输出格式(纯 JSON,无多余文字):\n"
            '{"relations": [{"relation_type": "<spawned-by|depends-on>", '
            '"confidence": 0.0-1.0, "rationale": "<简述>"}]}'
        )

    def _parse(
        self,
        raw: str,
        src_task_id: str,
        dst_task_id: str,
    ) -> List[RelationSuggestion]:
        """解析 LLM 响应 → 候选建议(未过滤 threshold)。健壮:容错非法 JSON / 字段。"""
        if not raw:
            return []
        # 抽取首个 JSON 对象(容 LLM 包裹 markdown/文字)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match is None:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        items = data.get("relations") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        out: List[RelationSuggestion] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rtype = item.get("relation_type")
            conf = item.get("confidence")
            if rtype not in RELATION_TYPES:
                continue
            try:
                conf_f = float(conf) if conf is not None else 0.0
            except (TypeError, ValueError):
                continue
            out.append(
                RelationSuggestion(
                    src_task_id=src_task_id,
                    dst_task_id=dst_task_id,
                    relation_type=str(rtype),
                    confidence=conf_f,
                    rationale=str(item.get("rationale", "")),
                )
            )
        return out
