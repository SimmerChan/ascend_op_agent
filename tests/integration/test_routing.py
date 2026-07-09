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

"""U7 路由集成测试 —— 跨 IntentClassifier + NudgeResponder + CalibrationRunner +
CLI ``task calibrate`` 端到端(KTD7 / KTD12 / KTD13 / #7 / #8)。

不依赖真 LLM:DI mock LLM callable + RuleBasedClassifier + CliRunner。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ascend_op_agent.cli import main
from ascend_op_agent.task_router.fixture import (
    CalibrationRunner,
    FixtureLoader,
    RuleBasedClassifier,
)
from ascend_op_agent.task_router.intent_classifier import (
    LABEL_NEW_TASK,
    LABEL_OFF_TASK,
    LABEL_ON_TASK,
    MODE_ALWAYS_ON_TASK,
    MODE_DEFAULT_ON,
    MODE_EXPLICIT_ONLY,
    ActiveTaskContext,
    IntentClassifier,
)
from ascend_op_agent.task_router.nudge import NUDGE_FIRM, NUDGE_SOFT, NudgeResponder

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "classifier_minimal.jsonl"


def _mock_llm(label, confidence=0.9, suggested=None):
    payload = {"label": label, "confidence": confidence}
    if suggested:
        payload["suggested_task_type"] = suggested

    def _call(prompt: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return _call


def _runnable_ctx(task_type="migrate"):
    return ActiveTaskContext(
        task_id="t_active", task_type=task_type, state="running", object_payload={"repo": "x"}
    )


# ---- Happy:on-task / off-task / new-task(AE1 + AE2) ----


def test_routing_on_task_executes_in_active_context():
    """AE1:on-task → 在 active context 执行(分类器返回 on-task,不牵引)。"""
    clf = IntentClassifier(llm_call=_mock_llm(LABEL_ON_TASK, 0.95))
    res = clf.route(MODE_DEFAULT_ON, "继续编译 add 算子", _runnable_ctx())
    assert res.label == LABEL_ON_TASK
    assert res.classified
    # on-task 不触发 nudge
    out = NudgeResponder(NUDGE_SOFT).respond(res, base_reply="ok", active_task_context=_runnable_ctx())
    assert not out.nudge_applied


def test_routing_off_task_soft_nudge():
    """AE1:off-task 明显闲聊 → 简答 + 软牵引。"""
    clf = IntentClassifier(llm_call=_mock_llm(LABEL_OFF_TASK, 0.97))
    res = clf.route(MODE_DEFAULT_ON, "你好呀天气怎么样", _runnable_ctx())
    assert res.label == LABEL_OFF_TASK
    out = NudgeResponder(NUDGE_SOFT).respond(res, base_reply="你好~", active_task_context=_runnable_ctx())
    assert out.nudge_applied
    assert "active task" in out.text


def test_routing_new_task_decomposes_and_suggests_type():
    """AE2:new-task("帮我迁这个模型")→ 拆解建 task(suggested_task_type=migrate)。"""
    clf = IntentClassifier(llm_call=_mock_llm(LABEL_NEW_TASK, 0.93, "migrate"))
    res = clf.route(MODE_DEFAULT_ON, "帮我迁这个模型")
    assert res.label == LABEL_NEW_TASK
    assert res.suggested_task_type == "migrate"


# ---- KTD7 三模式集成 ----


def test_routing_explicit_only_does_not_classify_freetext():
    """KTD7 集成:explicit-only 模式下 free-text 不分类(只显式命令生效)。

    即使有完美 LLM,explicit-only 也 return classified=False(交显式命令解析)。
    """
    clf = IntentClassifier(llm_call=_mock_llm(LABEL_OFF_TASK, 0.99))
    res = clf.route(MODE_EXPLICIT_ONLY, "随便一句闲聊", _runnable_ctx())
    assert res.classified is False
    assert res.label == ""


def test_routing_always_on_task_treats_freetext_as_on_task():
    """#7 集成:always-on-task 模式下 free-text → on-task(跳过 LLM 分类)。

    即使输入是明显闲聊 + LLM 会判 off-task,always-on-task 也强制 on-task 不调 LLM。
    """
    llm_calls = {"n": 0}

    def llm(prompt):
        llm_calls["n"] += 1
        return json.dumps({"label": LABEL_OFF_TASK, "confidence": 0.99})

    clf = IntentClassifier(llm_call=llm)
    res = clf.route(MODE_ALWAYS_ON_TASK, "你好呀天气怎么样讲个笑话", _runnable_ctx())
    assert res.label == LABEL_ON_TASK
    assert res.classified
    assert llm_calls["n"] == 0  # 跳过 LLM


def test_routing_firm_nudge_blocks_back_to_active():
    """R6 firm:off-task → 简答 + 阻断回 active task。"""
    clf = IntentClassifier(llm_call=_mock_llm(LABEL_OFF_TASK, 0.9))
    res = clf.route(MODE_DEFAULT_ON, "讲个笑话", _runnable_ctx())
    out = NudgeResponder(NUDGE_FIRM).respond(res, base_reply="没有笑话", active_task_context=_runnable_ctx())
    assert out.nudge_applied
    assert "/task progress" in out.text


# ---- KTD12 CLI 集成 ----


def test_cli_task_calibrate_rule_based_prints_report():
    """KTD12 集成:task calibrate --fixture <ship fixture> --rule-based → accuracy 报告。"""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["task", "calibrate", "--fixture", str(FIXTURE_PATH), "--rule-based"],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "accuracy" in out
    assert "per-label" in out
    assert "confusion matrix" in out
    # 报告了 example 数
    assert "20 examples" in out


def test_cli_task_calibrate_missing_fixture_fails():
    runner = CliRunner()
    result = runner.invoke(
        main, ["task", "calibrate", "--fixture", "/nonexistent/xxx.jsonl"]
    )
    # click.Path(exists=True) 在 parse 阶段报错 → exit_code != 0
    assert result.exit_code != 0


def test_cli_task_group_accepts_classifier_fixture_flag():
    """``--classifier-fixture`` opt-in flag 在 task group 接受(KTD12 入口)。"""
    runner = CliRunner()
    # 不实际用 fixture,只验 flag 被 group 接受(走 list 子命令,无 task 也 ok)
    result = runner.invoke(
        main,
        ["task", "--classifier-fixture", str(FIXTURE_PATH), "list"],
        standalone_mode=False,
    )
    # list 可能因无 db 抛错,但不应是 click UsageError(flag 被识别)
    # exit_code 0 或 1 都行,只要不是 "no such option" usage error
    assert "no such option" not in result.output.lower()


# ---- CalibrationRunner + fixture 端到端(#8 覆盖) ----


def test_calibration_runs_full_fixture_and_reports_all_labels():
    """KTD12:CalibrationRunner 在 ship fixture 上跑通,4 label 全可达。"""
    examples = FixtureLoader.load(FIXTURE_PATH)
    rep = CalibrationRunner(RuleBasedClassifier()).run(examples)
    assert rep.total == 20
    # 4 label 在 confusion matrix 行可达
    assert set(rep.confusion_matrix.keys()) == {
        LABEL_ON_TASK, LABEL_OFF_TASK, LABEL_NEW_TASK, "progress-query"
    }
    # 报告文本含全部 label
    txt = rep.format_text()
    for l in (LABEL_ON_TASK, LABEL_OFF_TASK, LABEL_NEW_TASK, "progress-query"):
        assert l in txt
