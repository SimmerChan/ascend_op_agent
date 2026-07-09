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

"""U7 pluggable fixture 框架(KTD12)。

3 组件:
  - ``LabelSchema`` / ``LabeledExample``:一条标注(input + active_task_id + label +
    nudge + confidence)。
  - ``FixtureLoader``:读 JSONL,validate schema → ``list[LabeledExample]``。
  - ``CalibrationRunner``:对每 example 调 ``classifier.classify`` →
    ``CalibrationReport``(accuracy / confusion_matrix / per_label precision/recall)。

附加 ``RuleBasedClassifier``:确定性 keyword 分类器(非 LLM),供离线 fixture sanity
与 ``task calibrate --rule-based`` / CI 确定性跑(无 LLM 环境也能验证框架跑通)。
**它不是 U7 LLM classifier**;LLM classifier 见 ``intent_classifier.py``。

fixture 数据/代码分离(KTD12 rationale):改 prompt → 重标 fixture;pluggable 框架
不绑版本。80% 校准门槛是 follow-up plan,本 unit 仅 ship 框架 + happy-path 通。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

from ascend_op_agent.task_router.intent_classifier import (
    LABELS,
    LABEL_NEW_TASK,
    LABEL_OFF_TASK,
    LABEL_ON_TASK,
    LABEL_PROGRESS_QUERY,
    ActiveTaskContext,
    ClassificationResult,
)
from ascend_op_agent.task_store.models import STATE_RUNNING


@dataclass
class LabeledExample:
    """一条标注(KTD12 LabelSchema)。

    Attributes:
        input: 用户输入文本。
        label: 期望 label(4 label 之一;fallback 场景标其 outcome label)。
        active_task_id: 关联 active task id;存在则 calibration 视 active 可运行
            (fallback→on-task),None 则 fallback→disambiguation(#8 覆盖)。
        nudge: off-task 时是否期望牵引(可选,本 unit 不强校验,留给 follow-up)。
        confidence: 期望置信度(可选,CalibrationRunner 不参与 accuracy 计算)。
    """

    input: str
    label: str
    active_task_id: Optional[str] = None
    nudge: Optional[bool] = None
    confidence: Optional[float] = None


class FixtureError(Exception):
    """fixture 解析/校验错误。"""


class FixtureLoader:
    """JSONL fixture 加载器(KTD12)。"""

    @staticmethod
    def load(path: Union[str, Path]) -> List[LabeledExample]:
        """读 JSONL → ``list[LabeledExample]``。validate label ∈ 4 label。

        Raises:
            FileNotFoundError: 文件不存在。
            FixtureError: 空文件 / 行非 JSON 对象 / label 非法 / 缺必填字段。
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"fixture not found: {p}")
        examples: List[LabeledExample] = []
        seen_header = False
        with open(p, "r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue  # 容空行 / 注释行
                seen_header = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise FixtureError(f"line {lineno}: invalid JSON ({e})") from e
                if not isinstance(obj, dict):
                    raise FixtureError(f"line {lineno}: not a JSON object")
                inp = obj.get("input")
                label = obj.get("label")
                if not isinstance(inp, str) or not inp:
                    raise FixtureError(f"line {lineno}: missing 'input' string")
                if not isinstance(label, str):
                    raise FixtureError(f"line {lineno}: missing 'label' string")
                if label not in LABELS:
                    raise FixtureError(
                        f"line {lineno}: unknown label '{label}'; expected one of {LABELS}"
                    )
                examples.append(
                    LabeledExample(
                        input=inp,
                        label=label,
                        active_task_id=obj.get("active_task_id"),
                        nudge=obj.get("nudge"),
                        confidence=obj.get("confidence"),
                    )
                )
        if not seen_header:
            raise FixtureError(f"fixture empty: {p}")
        return examples


@dataclass
class CalibrationReport:
    """CalibrationRunner 输出(KTD12)。

    Attributes:
        total / correct: example 总数 / label 命中数。
        accuracy: correct / total。
        confusion_matrix: ``{true_label: {pred_label: count}}``。
        per_label: ``{label: {precision, recall, f1, support}}``。
        mismatches: 错分样例 ``{input, expected, predicted}``。
    """

    total: int
    correct: int
    accuracy: float
    confusion_matrix: Dict[str, Dict[str, int]]
    per_label: Dict[str, Dict[str, float]]
    mismatches: List[dict] = field(default_factory=list)

    def format_text(self) -> str:
        """人读报告(CLI ``task calibrate`` 打印)。"""
        lines = [
            f"accuracy: {self.accuracy:.2%} ({self.correct}/{self.total})",
            "",
            "per-label (precision/recall/f1/support):",
        ]
        for label in LABELS:
            m = self.per_label.get(label, {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0})
            lines.append(
                f"  {label:14} p={m['precision']:.2f} r={m['recall']:.2f} "
                f"f1={m['f1']:.2f} n={int(m['support'])}"
            )
        lines.append("")
        lines.append("confusion matrix (rows=true, cols=pred):")
        header = "  " + " ".join(f"{l[:6]:>6}" for l in LABELS)
        lines.append(header)
        for true_l in LABELS:
            row = self.confusion_matrix.get(true_l, {})
            cells = " ".join(f"{row.get(p, 0):>6}" for p in LABELS)
            lines.append(f"  {true_l[:6]:<6} {cells}")
        if self.mismatches:
            lines.append("")
            lines.append(f"mismatches ({len(self.mismatches)}):")
            for mm in self.mismatches[:10]:
                lines.append(
                    f"  expected={mm['expected']} predicted={mm['predicted']} "
                    f"input={mm['input']!r}"
                )
        return "\n".join(lines)


class CalibrationRunner:
    """跑 classifier vs fixture 产 CalibrationReport(KTD12)。

    Args:
        classifier: 有 ``classify(user_input, active_task_context) -> ClassificationResult``
            的对象(IntentClassifier 或 RuleBasedClassifier)。
    """

    def __init__(self, classifier):
        self.classifier = classifier

    def run(self, examples: List[LabeledExample]) -> CalibrationReport:
        """对每 example 调 classify,对比 label 产报告。"""
        confusion: Dict[str, Dict[str, int]] = {t: {p: 0 for p in LABELS} for t in LABELS}
        tp = {l: 0 for l in LABELS}
        fp = {l: 0 for l in LABELS}
        fn = {l: 0 for l in LABELS}
        support = {l: 0 for l in LABELS}
        correct = 0
        mismatches: List[dict] = []

        for ex in examples:
            # fixture 的 active_task_id 存在 → 视 active 可运行(fallback→on-task);
            # None → active 不可运行(fallback→disambiguation,#8 覆盖)。
            ctx = (
                ActiveTaskContext(
                    task_id=ex.active_task_id, task_type="", state=STATE_RUNNING
                )
                if ex.active_task_id
                else None
            )
            result: ClassificationResult = self.classifier.classify(ex.input, ctx)
            pred = result.label if result.label in LABELS else LABEL_ON_TASK
            true = ex.label
            confusion[true][pred] = confusion[true].get(pred, 0) + 1
            support[true] += 1
            if pred == true:
                correct += 1
                tp[true] += 1
            else:
                fp[pred] += 1
                fn[true] += 1
                mismatches.append(
                    {"input": ex.input, "expected": true, "predicted": pred}
                )

        total = len(examples)
        accuracy = correct / total if total else 0.0
        per_label: Dict[str, Dict[str, float]] = {}
        for l in LABELS:
            precision = tp[l] / (tp[l] + fp[l]) if (tp[l] + fp[l]) else 0.0
            recall = tp[l] / (tp[l] + fn[l]) if (tp[l] + fn[l]) else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall)
                else 0.0
            )
            per_label[l] = {
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": float(support[l]),
            }
        return CalibrationReport(
            total=total,
            correct=correct,
            accuracy=accuracy,
            confusion_matrix=confusion,
            per_label=per_label,
            mismatches=mismatches,
        )


class RuleBasedClassifier:
    """确定性 keyword 分类器(非 LLM,供离线 fixture sanity + CI 确定性跑)。

    实现 ``classify(user_input, active_task_context)`` 同形接口 —— CalibrationRunner
    可直接喂。规则:
      - 含进度词(进度/到哪/状态/progress)→ progress-query
      - 含新任务词(迁/迁移/migrate/分析/analyze/优化/optimize/新任务)→ new-task
      - 含闲聊词(你好/hi/hello/谢谢/天气/笑话)→ off-task
      - 否则 → on-task(默认推进 active task)
    置信度固定 0.9(高置信,不走 fallback)—— 仅供框架跑通,非生产分类质量。
    """

    _RULES = (
        (LABEL_PROGRESS_QUERY, ("进度", "到哪", "状态", "progress", "现在到", "怎么样了")),
        (LABEL_NEW_TASK, ("迁", "迁移", "migrate", "分析", "analyze", "优化", "optimize",
                          "新任务", "帮我", "开个")),
        (LABEL_OFF_TASK, ("你好", "hi", "hello", "嗨", "谢谢", "天气", "笑话", "你是谁")),
    )

    def classify(
        self,
        user_input: str,
        active_task_context: Optional[ActiveTaskContext] = None,
    ) -> ClassificationResult:
        text = (user_input or "").lower()
        for label, keywords in self._RULES:
            if any(kw.lower() in text for kw in keywords):
                suggested = self._guess_task_type(user_input) if label == LABEL_NEW_TASK else None
                return ClassificationResult(
                    label=label, confidence=0.9, suggested_task_type=suggested
                )
        return ClassificationResult(label=LABEL_ON_TASK, confidence=0.9)

    @staticmethod
    def _guess_task_type(text: str) -> Optional[str]:
        low = text.lower()
        if any(k in low for k in ("迁", "迁移", "migrate")):
            return "migrate"
        if any(k in low for k in ("分析", "analyze")):
            return "analyze"
        if any(k in low for k in ("优化", "optimize")):
            return "optimize"
        return "develop"
