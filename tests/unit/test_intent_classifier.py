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

"""U7 IntentClassifier 单测(R5 + KTD7 + KTD13 + #7 + #8)。

mock LLM callable(依赖注入),覆盖:
- Happy:4 label 各一(on-task / off-task / new-task / progress-query)
- Happy:new-task → suggested_task_type(AE2)
- Edge:低置信 → fallback on-active-task(active 可运行)
- Edge:低置信 + null/不可运行 active → disambiguation(#8)
- Edge:LLM 不可用(None)→ fallback
- Error:LLM 超时(真超时 + mock raise)→ fallback
- Error:LLM 返非法 JSON → fallback(parse-failure)
- Integration:KTD7 三模式 route(explicit-only defer / always-on-task 跳分类 /
  default-on 真分类)
"""

from __future__ import annotations

import json
import time

import pytest

from ascend_op_agent.task_router.intent_classifier import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    LABEL_NEW_TASK,
    LABEL_OFF_TASK,
    LABEL_ON_TASK,
    LABEL_PROGRESS_QUERY,
    MODE_ALWAYS_ON_TASK,
    MODE_DEFAULT_ON,
    MODE_EXPLICIT_ONLY,
    ActiveTaskContext,
    ClassificationResult,
    IntentClassifier,
)


def _mock_llm(payload: dict):
    """返回 mock LLM callable,固定输出给定 payload(JSON)。"""

    def _call(prompt: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return _call


def _runnable_ctx(task_id="t_active", task_type="develop"):
    return ActiveTaskContext(
        task_id=task_id, task_type=task_type, state="running", object_payload={"op": "add"}
    )


def _unrunnable_ctx(task_id="t_done", task_type="develop", state="done"):
    return ActiveTaskContext(task_id=task_id, task_type=task_type, state=state)


# ---- Happy:4 label ----


def test_classify_on_task():
    """AE1:on-task 输入 → on-task,在 active context 执行。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "on-task", "confidence": 0.95})
    )
    res = clf.classify("继续编译 add 算子", _runnable_ctx())
    assert res.label == LABEL_ON_TASK
    assert res.confidence == pytest.approx(0.95)
    assert not res.fallback
    assert res.classified


def test_classify_off_task():
    """AE1:off-task 闲聊 → off-task(caller 加软牵引)。"""
    clf = IntentClassifier(llm_call=_mock_llm({"label": "off-task", "confidence": 0.97}))
    res = clf.classify("你好呀", _runnable_ctx())
    assert res.label == LABEL_OFF_TASK
    assert not res.fallback


def test_classify_new_task_with_suggested_type():
    """AE2:new-task → suggested_task_type=R7 拆解。"""
    clf = IntentClassifier(
        llm_call=_mock_llm(
            {"label": "new-task", "confidence": 0.93, "suggested_task_type": "migrate"}
        )
    )
    res = clf.classify("帮我迁这个模型")
    assert res.label == LABEL_NEW_TASK
    assert res.suggested_task_type == "migrate"


def test_classify_progress_query():
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "progress-query", "confidence": 0.9})
    )
    res = clf.classify("现在到哪了")
    assert res.label == LABEL_PROGRESS_QUERY


# ---- Edge:低置信 fallback ----


def test_classify_low_confidence_falls_back_on_active_task():
    """Edge:低置信(< 0.6)→ fallback 默认 on-active-task(KTD13)。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "off-task", "confidence": 0.3}),
        confidence_threshold=0.6,
    )
    res = clf.classify("那个东西", _runnable_ctx())
    assert res.fallback
    assert "low-confidence" in res.fallback_reason
    # KTD13:默认 on-active-task(active 可运行)
    assert res.label == LABEL_ON_TASK
    assert not res.disambiguate


def test_classify_threshold_boundary_kept():
    """confidence == threshold 应保留(>=),不 fallback。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "on-task", "confidence": 0.6}),
        confidence_threshold=0.6,
    )
    res = clf.classify("继续", _runnable_ctx())
    assert not res.fallback
    assert res.label == LABEL_ON_TASK


# ---- #8: null / 不可运行 active + fallback → disambiguation ----


def test_low_confidence_null_active_disambiguates():
    """#8:null active + 低置信 → disambiguation(非 on-active-task)。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "off-task", "confidence": 0.2})
    )
    res = clf.classify("呃随便吧", active_task_context=None)
    assert res.fallback
    assert res.disambiguate
    assert res.disambiguation_prompt is not None
    # lean new-task,但不 silent spawn(disambiguate=True 优先)
    assert res.label == LABEL_NEW_TASK


def test_low_confidence_unrunnable_active_disambiguates():
    """#8:active 状态 done(不可运行)+ 低置信 → disambiguation。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "off-task", "confidence": 0.2})
    )
    res = clf.classify("嗯", active_task_context=_unrunnable_ctx(state="done"))
    assert res.fallback
    assert res.disambiguate
    for bad_state in ("failed", "paused"):
        res2 = clf.classify("嗯", active_task_context=_unrunnable_ctx(state=bad_state))
        assert res2.disambiguate, f"state={bad_state} should disambiguate"


def test_runnable_property():
    """ActiveTaskContext.runnable = state ∈ {draft, running}。"""
    assert ActiveTaskContext("t", state="draft").runnable is True
    assert ActiveTaskContext("t", state="running").runnable is True
    assert ActiveTaskContext("t", state="done").runnable is False
    assert ActiveTaskContext("t", state="failed").runnable is False
    assert ActiveTaskContext("t", state="paused").runnable is False
    assert ActiveTaskContext("t", state="").runnable is False


# ---- Error:LLM 不可用 / 超时 / 解析失败 ----


def test_classify_no_llm_falls_back():
    """KTD13:LLM 未 wiring → fallback。"""
    clf = IntentClassifier(llm_call=None)
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "llm-unavailable"
    assert res.label == LABEL_ON_TASK  # active 可运行


def test_classify_no_llm_null_active_disambiguates():
    """KTD13 + #8:无 LLM + null active → disambiguation。"""
    clf = IntentClassifier(llm_call=None)
    res = clf.classify("anything", active_task_context=None)
    assert res.fallback
    assert res.disambiguate


def test_classify_llm_timeout_mock_raise():
    """KTD13:LLM callable raise TimeoutError → fallback(reason=timeout)。"""
    def slow_llm(prompt: str) -> str:
        raise TimeoutError("simulated")

    clf = IntentClassifier(llm_call=slow_llm, timeout_s=5.0)
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "timeout"
    assert res.label == LABEL_ON_TASK


def test_classify_llm_timeout_real_enforced():
    """KTD13:真超时(sleep > timeout_s)被 ThreadPoolExecutor 兜住 → fallback。

    用小 timeout_s + 短 sleep,~0.3s 完成不拖慢 suite。
    """
    def slow_llm(prompt: str) -> str:
        time.sleep(0.3)
        return json.dumps({"label": "on-task", "confidence": 0.9})

    clf = IntentClassifier(llm_call=slow_llm, timeout_s=0.1)
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "timeout"


def test_classify_llm_timeout_null_active_disambiguates():
    """#8:timeout + null active → disambiguation。"""
    def llm(prompt: str) -> str:
        raise TimeoutError

    clf = IntentClassifier(llm_call=llm)
    res = clf.classify("anything", active_task_context=None)
    assert res.fallback
    assert res.fallback_reason == "timeout"
    assert res.disambiguate


def test_classify_llm_generic_error_falls_back():
    """KTD13:LLM 抛任意异常 → fallback(llm-unavailable),不崩。"""
    def bad_llm(prompt: str) -> str:
        raise ConnectionError("api down")

    clf = IntentClassifier(llm_call=bad_llm)
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "llm-unavailable"


def test_classify_parse_failure_falls_back():
    """KTD13:LLM 返非法 JSON → fallback(parse-failure)。"""
    clf = IntentClassifier(llm_call=lambda p: "not json at all")
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "parse-failure"


def test_classify_parse_markdown_wrapped_json():
    """Robust:LLM 返 markdown code-fence 包裹 JSON → 仍解析。"""
    def llm(prompt: str) -> str:
        return (
            "```json\n"
            + json.dumps({"label": "on-task", "confidence": 0.88})
            + "\n```"
        )

    clf = IntentClassifier(llm_call=llm)
    res = clf.classify("继续")
    assert res.label == LABEL_ON_TASK
    assert not res.fallback


def test_classify_unknown_label_filtered_to_parse_failure():
    """LLM 输出非法 label(如 chitchat)→ parse 返 None → fallback。"""
    clf = IntentClassifier(
        llm_call=_mock_llm({"label": "chitchat", "confidence": 0.9})
    )
    res = clf.classify("anything", _runnable_ctx())
    assert res.fallback
    assert res.fallback_reason == "parse-failure"


def test_classify_invalid_suggested_task_type_nulled():
    """suggested_task_type 非 TASK_TYPES → None,其余保留(label 仍 on-task)。"""
    clf = IntentClassifier(
        llm_call=_mock_llm(
            {"label": "new-task", "confidence": 0.9, "suggested_task_type": "bogus"}
        )
    )
    res = clf.classify("帮我")
    assert res.label == LABEL_NEW_TASK
    assert res.suggested_task_type is None


def test_classify_never_raises():
    """R5 兜底:任何输入/任何 LLM 行为,classify 永不抛。"""
    clf = IntentClassifier(llm_call=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    res = clf.classify("anything")  # 不应抛
    assert isinstance(res, ClassificationResult)
    assert res.fallback


# ---- KTD7 三模式 route(#7) ----


def test_route_explicit_only_defers():
    """KTD7:explicit-only → free-text 不分类(classified=False)。"""
    clf = IntentClassifier(llm_call=_mock_llm({"label": "on-task", "confidence": 0.99}))
    res = clf.route(MODE_EXPLICIT_ONLY, "随便一句话", _runnable_ctx())
    assert res.classified is False
    assert res.label == ""
    # 不应调 LLM(无意义):explicit-only 永远 defer


def test_route_always_on_task_skips_classifier():
    """#7:always-on-task → 所有 free-text 当 on-task(跳过 LLM)。"""
    called = {"n": 0}

    def llm(prompt: str) -> str:
        called["n"] += 1
        return json.dumps({"label": "off-task", "confidence": 0.99})

    clf = IntentClassifier(llm_call=llm)
    res = clf.route(MODE_ALWAYS_ON_TASK, "你好呀天气怎么样", _runnable_ctx())
    assert res.classified
    assert res.label == LABEL_ON_TASK  # 跳过 LLM,不当 off-task
    assert called["n"] == 0  # LLM 未被调用
    assert "always-on-task-mode" in (res.fallback_reason or "")


def test_route_default_on_uses_classifier():
    """default-on → 真走 classify(LLM + fallback)。"""
    clf = IntentClassifier(llm_call=_mock_llm({"label": "off-task", "confidence": 0.95}))
    res = clf.route(MODE_DEFAULT_ON, "你好", _runnable_ctx())
    assert res.label == LABEL_OFF_TASK
    assert res.classified


def test_route_unknown_mode_raises():
    clf = IntentClassifier()
    with pytest.raises(ValueError, match="unknown default_mode"):
        clf.route("bogus", "x")


def test_default_threshold_value():
    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.6
