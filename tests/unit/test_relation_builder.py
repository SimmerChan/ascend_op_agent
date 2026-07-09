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

"""U5 RelationBuilder 单测(KTD4 suggest + confirm,不落库)。

mock LLM callable(依赖注入),覆盖:
- Happy:新 analyze task(active=migrate)→ LLM suggest spawned-by migrate(AE3)
- Edge:LLM 低置信(< 0.6)→ 不 suggest
- Edge:无 active / active==new / 无参照 → 空
- Edge:无 LLM wiring → 空
- Robust:LLM 返回 markdown 包裹 / 非法 JSON → 容错
"""

from __future__ import annotations

import json

import pytest

from ascend_op_agent.task_router.relation_builder import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    RelationBuilder,
    RelationSuggestion,
)
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TaskStore,
)


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def _mock_llm(payload: dict):
    """返回一个 mock LLM callable,固定输出给定 payload(JSON)。"""

    def _call(prompt: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return _call


def test_suggest_happy_spawned_by(store):
    """AE3:新 analyze(active=migrate)→ LLM suggest spawned-by migrate。"""
    migrate_id = store.create_task(TASK_TYPE_MIGRATE, {"repo": "model_x"})
    analyze_id = store.create_task(TASK_TYPE_ANALYZE, {"op": "matmul"})
    store.set_active(migrate_id)

    llm = _mock_llm(
        {
            "relations": [
                {
                    "relation_type": "spawned-by",
                    "confidence": 0.9,
                    "rationale": "analyze 派生自迁移中瓶颈",
                }
            ]
        }
    )
    builder = RelationBuilder(store, llm_call=llm)
    suggestions = builder.suggest(analyze_id)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.src_task_id == analyze_id
    assert s.dst_task_id == migrate_id
    assert s.relation_type == "spawned-by"
    assert s.confidence == 0.9
    # KTD4:不落库
    assert store.get_task(analyze_id) is not None  # task 还在


def test_suggest_low_confidence_filtered(store):
    """Edge:LLM 低置信(< 0.6)→ 不 suggest。"""
    migrate_id = store.create_task(TASK_TYPE_MIGRATE, {"repo": "x"})
    analyze_id = store.create_task(TASK_TYPE_ANALYZE, {"op": "add"})
    store.set_active(migrate_id)
    llm = _mock_llm(
        {"relations": [{"relation_type": "spawned-by", "confidence": 0.3}]}
    )
    builder = RelationBuilder(store, llm_call=llm)
    assert builder.suggest(analyze_id) == []


def test_suggest_threshold_boundary(store):
    """threshold 边界:confidence == threshold 应保留(>=)。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    llm = _mock_llm(
        {"relations": [{"relation_type": "spawned-by", "confidence": 0.6}]}
    )
    builder = RelationBuilder(store, llm_call=llm)
    suggestions = builder.suggest(b)
    assert len(suggestions) == 1
    assert suggestions[0].confidence == pytest.approx(0.6)


def test_suggest_custom_threshold(store):
    """自定义 threshold=0.8 → 0.7 被滤掉。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    llm = _mock_llm(
        {"relations": [{"relation_type": "spawned-by", "confidence": 0.7}]}
    )
    builder = RelationBuilder(store, llm_call=llm, confidence_threshold=0.8)
    assert builder.suggest(b) == []


def test_suggest_no_active_returns_empty(store):
    """Edge:无 active → 空(无参照)。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    llm = _mock_llm({"relations": [{"relation_type": "spawned-by", "confidence": 0.9}]})
    builder = RelationBuilder(store, llm_call=llm)
    assert builder.suggest(a) == []


def test_suggest_active_equals_new_returns_empty(store):
    """Edge:active == new_task → 空(自指)。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    llm = _mock_llm({"relations": [{"relation_type": "spawned-by", "confidence": 0.9}]})
    builder = RelationBuilder(store, llm_call=llm)
    assert builder.suggest(a) == []


def test_suggest_explicit_active_overrides(store):
    """显式 active_task_id 优先于 store.get_active。"""
    a = store.create_task(TASK_TYPE_DEVELOP, {"name": "A"})
    b = store.create_task(TASK_TYPE_DEVELOP, {"name": "B"})
    c = store.create_task(TASK_TYPE_DEVELOP, {"name": "C"})
    store.set_active(a)
    llm = _mock_llm({"relations": [{"relation_type": "depends-on", "confidence": 0.8}]})
    builder = RelationBuilder(store, llm_call=llm)
    suggestions = builder.suggest(c, active_task_id=b)
    assert len(suggestions) == 1
    assert suggestions[0].dst_task_id == b  # 用显式 b 而非 active a


def test_suggest_no_llm_returns_empty(store):
    """Edge:未 wiring LLM → 空(优雅降级,不崩)。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    builder = RelationBuilder(store, llm_call=None)
    assert builder.suggest(b) == []


def test_suggest_unknown_task_raises(store):
    a = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    builder = RelationBuilder(store, llm_call=_mock_llm({"relations": []}))
    with pytest.raises(KeyError, match="unknown task"):
        builder.suggest("phantom")


def test_suggest_parse_markdown_wrapped(store):
    """Robust:LLM 返回 markdown code-fence 包裹的 JSON → 仍解析。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)

    def llm(prompt: str) -> str:
        return (
            "```json\n"
            + json.dumps(
                {
                    "relations": [
                        {"relation_type": "depends-on", "confidence": 0.85}
                    ]
                }
            )
            + "\n```"
        )

    builder = RelationBuilder(store, llm_call=llm)
    suggestions = builder.suggest(b)
    assert len(suggestions) == 1
    assert suggestions[0].relation_type == "depends-on"


def test_suggest_parse_malformed_returns_empty(store):
    """Robust:LLM 返回非法 JSON → 不崩,返回空。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    builder = RelationBuilder(store, llm_call=lambda p: "not json at all")
    assert builder.suggest(b) == []


def test_suggest_parse_unknown_relation_type_filtered(store):
    """LLM 输出非一期 type(如 blocks)→ 滤掉。"""
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    store.set_active(a)
    llm = _mock_llm(
        {
            "relations": [
                {"relation_type": "blocks", "confidence": 0.9},
                {"relation_type": "spawned-by", "confidence": 0.8},
            ]
        }
    )
    builder = RelationBuilder(store, llm_call=llm)
    suggestions = builder.suggest(b)
    assert len(suggestions) == 1
    assert suggestions[0].relation_type == "spawned-by"


def test_default_threshold_value():
    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.6
