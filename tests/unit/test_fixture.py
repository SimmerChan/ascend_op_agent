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

"""U7 fixture 框架单测(KTD12 FixtureLoader + CalibrationRunner + RuleBasedClassifier)。

用确定性 mock classifier(StubClassifier)验证 accuracy / confusion_matrix /
per_label precision/recall/f1 计算,再用 RuleBasedClassifier 跑真实 fixture。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend_op_agent.task_router.fixture import (
    CalibrationRunner,
    FixtureError,
    FixtureLoader,
    LabeledExample,
    RuleBasedClassifier,
)
from ascend_op_agent.task_router.intent_classifier import (
    LABELS,
    LABEL_NEW_TASK,
    LABEL_OFF_TASK,
    LABEL_ON_TASK,
    LABEL_PROGRESS_QUERY,
    ActiveTaskContext,
    ClassificationResult,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "classifier_minimal.jsonl"


# ---- Stub classifier:按预定 label 映射返回(验证 CalibrationRunner 计算) ----


class StubClassifier:
    """确定性 stub —— 按 input → label 映射返回,验证 report 计算。"""

    def __init__(self, mapping: dict, default=LABEL_ON_TASK):
        self.mapping = mapping
        self.default = default

    def classify(self, user_input, active_task_context=None):
        label = self.mapping.get(user_input, self.default)
        return ClassificationResult(label=label, confidence=0.9)


# ---- FixtureLoader ----


def test_loader_loads_minimal_fixture():
    """KTD12 框架跑通:加载 ship fixture,20 example,4 label 全覆盖。"""
    examples = FixtureLoader.load(FIXTURE_PATH)
    assert len(examples) == 20
    labels = {e.label for e in examples}
    assert labels == set(LABELS)  # 4 label 全有
    # 低置信 fallback + timeout fallback 各 1(outcome label = on-task,见 fixture 注释)
    on_task = [e for e in examples if e.label == LABEL_ON_TASK]
    assert len(on_task) >= 5


def test_loader_ignores_comments_and_blanks(tmp_path):
    f = tmp_path / "f.jsonl"
    f.write_text(
        "# a comment\n"
        "\n"
        '{"input": "hi", "label": "off-task"}\n'
        "  \n"
        '{"input": "go", "label": "on-task"}\n',
        encoding="utf-8",
    )
    exs = FixtureLoader.load(f)
    assert len(exs) == 2


def test_loader_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        FixtureLoader.load(tmp_path / "nope.jsonl")


def test_loader_empty_file_raises(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_text("# only comment\n\n", encoding="utf-8")
    with pytest.raises(FixtureError, match="empty"):
        FixtureLoader.load(f)


def test_loader_invalid_json_raises(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(FixtureError, match="invalid JSON"):
        FixtureLoader.load(f)


def test_loader_unknown_label_raises(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(json.dumps({"input": "x", "label": "chitchat"}) + "\n", encoding="utf-8")
    with pytest.raises(FixtureError, match="unknown label"):
        FixtureLoader.load(f)


def test_loader_missing_input_raises(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text(json.dumps({"label": "on-task"}) + "\n", encoding="utf-8")
    with pytest.raises(FixtureError, match="missing 'input'"):
        FixtureLoader.load(f)


def test_loader_allows_extra_fields(tmp_path):
    """_task_type / _note 等额外字段被忽略。"""
    f = tmp_path / "f.jsonl"
    f.write_text(
        json.dumps(
            {"input": "x", "label": "on-task", "_note": "ok", "active_task_id": "t1"}
        )
        + "\n",
        encoding="utf-8",
    )
    exs = FixtureLoader.load(f)
    assert len(exs) == 1
    assert exs[0].active_task_id == "t1"


# ---- CalibrationRunner(用 StubClassifier 验证计算) ----


def test_calibration_perfect_accuracy():
    examples = [
        LabeledExample(input="a", label=LABEL_ON_TASK),
        LabeledExample(input="b", label=LABEL_OFF_TASK),
        LabeledExample(input="c", label=LABEL_NEW_TASK),
        LabeledExample(input="d", label=LABEL_PROGRESS_QUERY),
    ]
    stub = StubClassifier({"a": LABEL_ON_TASK, "b": LABEL_OFF_TASK,
                           "c": LABEL_NEW_TASK, "d": LABEL_PROGRESS_QUERY})
    rep = CalibrationRunner(stub).run(examples)
    assert rep.total == 4
    assert rep.correct == 4
    assert rep.accuracy == 1.0
    assert rep.mismatches == []
    # per_label 全 1.0
    for l in LABELS:
        assert rep.per_label[l]["precision"] == 1.0
        assert rep.per_label[l]["recall"] == 1.0
        assert rep.per_label[l]["f1"] == 1.0


def test_calibration_partial_accuracy_and_mismatches():
    examples = [
        LabeledExample(input="a", label=LABEL_ON_TASK),
        LabeledExample(input="b", label=LABEL_OFF_TASK),
        LabeledExample(input="c", label=LABEL_NEW_TASK),
    ]
    # stub 把 b 错判为 on-task
    stub = StubClassifier({"a": LABEL_ON_TASK, "b": LABEL_ON_TASK,
                           "c": LABEL_NEW_TASK})
    rep = CalibrationRunner(stub).run(examples)
    assert rep.total == 3
    assert rep.correct == 2
    assert rep.accuracy == pytest.approx(2 / 3)
    assert len(rep.mismatches) == 1
    assert rep.mismatches[0]["expected"] == LABEL_OFF_TASK
    assert rep.mismatches[0]["predicted"] == LABEL_ON_TASK


def test_calibration_confusion_matrix_shape():
    examples = [
        LabeledExample(input="a", label=LABEL_ON_TASK),
        LabeledExample(input="b", label=LABEL_ON_TASK),
        LabeledExample(input="c", label=LABEL_OFF_TASK),
    ]
    stub = StubClassifier({"a": LABEL_ON_TASK, "b": LABEL_OFF_TASK,
                           "c": LABEL_OFF_TASK})
    rep = CalibrationRunner(stub).run(examples)
    cm = rep.confusion_matrix
    # rows/cols 全 4 label
    assert set(cm.keys()) == set(LABELS)
    for row in cm.values():
        assert set(row.keys()) == set(LABELS)
    # on-task row: a 命中 on-task, b 错判 off-task
    assert cm[LABEL_ON_TASK][LABEL_ON_TASK] == 1
    assert cm[LABEL_ON_TASK][LABEL_OFF_TASK] == 1


def test_calibration_per_label_precision_recall():
    """off-task:1 true(b),但被 stub 判 on-task → off-task recall=0。"""
    examples = [
        LabeledExample(input="b", label=LABEL_OFF_TASK),
        LabeledExample(input="a", label=LABEL_ON_TASK),
    ]
    stub = StubClassifier({"b": LABEL_ON_TASK, "a": LABEL_ON_TASK})
    rep = CalibrationRunner(stub).run(examples)
    assert rep.per_label[LABEL_OFF_TASK]["recall"] == 0.0
    assert rep.per_label[LABEL_OFF_TASK]["support"] == 1.0
    # on-task: 1 tp(a), 1 fp(b) → precision 0.5, recall 1.0
    assert rep.per_label[LABEL_ON_TASK]["precision"] == 0.5
    assert rep.per_label[LABEL_ON_TASK]["recall"] == 1.0


def test_calibration_format_text_contains_accuracy():
    examples = [LabeledExample(input="a", label=LABEL_ON_TASK)]
    rep = CalibrationRunner(StubClassifier({"a": LABEL_ON_TASK})).run(examples)
    txt = rep.format_text()
    assert "accuracy" in txt
    assert "confusion matrix" in txt
    assert "per-label" in txt


def test_calibration_null_active_disambiguation_path():
    """CalibrationRunner 对 active_task_id=None 的 example 传 None ctx。

    配合一个总是低置信/无 LLM 的 classifier → 触发 #8 disambiguation(new-task lean)。
    验证 fixture 框架与 #8 路径兼容。
    """
    from ascend_op_agent.task_router.intent_classifier import IntentClassifier

    clf = IntentClassifier(llm_call=None)  # 无 LLM → fallback
    examples = [
        LabeledExample(input="x", label=LABEL_NEW_TASK, active_task_id=None),
        LabeledExample(input="y", label=LABEL_ON_TASK, active_task_id="t1"),
    ]
    rep = CalibrationRunner(clf).run(examples)
    # x: null active → disambiguation new-task lean → 命中 NEW_TASK
    # y: runnable active → fallback on-task → 命中 ON_TASK
    assert rep.correct == 2
    assert rep.accuracy == 1.0


# ---- RuleBasedClassifier + 真实 fixture(框架跑通证明) ----


def test_rule_based_classifier_labels():
    rb = RuleBasedClassifier()
    assert rb.classify("你好").label == LABEL_OFF_TASK
    assert rb.classify("帮我迁这个模型").label == LABEL_NEW_TASK
    assert rb.classify("帮我迁这个模型").suggested_task_type == "migrate"
    assert rb.classify("现在进度到哪了").label == LABEL_PROGRESS_QUERY
    assert rb.classify("继续编译算子").label == LABEL_ON_TASK


def test_rule_based_on_minimal_fixture_runs():
    """KTD12 ship 门槛:框架跑通(rule-based baseline 在 fixture 上跑出报告)。"""
    examples = FixtureLoader.load(FIXTURE_PATH)
    rep = CalibrationRunner(RuleBasedClassifier()).run(examples)
    # 框架跑通 = 有合法报告;不要求 ≥80%(follow-up)
    assert 0.0 <= rep.accuracy <= 1.0
    assert rep.total == 20
    # 4 label 在 confusion matrix 全可达
    assert set(rep.confusion_matrix.keys()) == set(LABELS)
