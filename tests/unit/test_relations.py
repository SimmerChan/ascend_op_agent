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

"""U5 RelationStore 单测(KTD9 adjacency + per-type cycle detection)。

覆盖 plan U5 Test scenarios 的 data-layer 部分:
- Happy:add / edit(in-place)/ remove / list / get
- Edge:同 src→dst 同 type 重复 add 幂等;per-type DFS 隔离(mixed-type 不成环)
- Error:同 type 成环 → RelationCycleError;edit_relation 目标不存在 → raise
- Integration:跨实例持久化(reopen RelationStore,relation 仍可读)
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_store.relations import (
    CREATED_BY_MANUAL,
    RELATION_DEPENDS_ON,
    RELATION_SPAWNED_BY,
    RelationCycleError,
    RelationNotFoundError,
    RelationStore,
)


@pytest.fixture
def store(tmp_path):
    return RelationStore(tmp_path / "tasks.db")


def test_add_and_get_relation(store):
    rel = store.add_relation("a", "b", RELATION_SPAWNED_BY, confidence=0.7)
    assert rel.src_task_id == "a"
    assert rel.dst_task_id == "b"
    assert rel.relation_type == RELATION_SPAWNED_BY
    assert rel.confidence == 0.7
    assert rel.created_by == CREATED_BY_MANUAL
    assert rel.created_at != ""
    got = store.get_relation("a", "b", RELATION_SPAWNED_BY)
    assert got is not None
    assert got.src_task_id == "a"


def test_add_relation_rejects_unknown_type(store):
    with pytest.raises(ValueError, match="unknown relation type"):
        store.add_relation("a", "b", "blocks")


def test_add_relation_idempotent_same_src_dst_type(store):
    """Edge:同 src→dst 同 type 重复 add → 幂等(无重复行)。"""
    r1 = store.add_relation("a", "b", RELATION_DEPENDS_ON, confidence=0.5)
    r2 = store.add_relation("a", "b", RELATION_DEPENDS_ON, confidence=0.9)
    assert r1 == r2  # 返回既有行,confidence 不变
    assert r2.confidence == 0.5
    assert store.get_relation("a", "b", RELATION_DEPENDS_ON) is not None
    # 不同 type 同对则可共存(KTD9 PK 含 type)
    r3 = store.add_relation("a", "b", RELATION_SPAWNED_BY)
    assert r3.relation_type == RELATION_SPAWNED_BY
    rels = store.list_relations("a")
    assert {r.relation_type for r in rels} == {RELATION_DEPENDS_ON, RELATION_SPAWNED_BY}


def test_cycle_detected_same_type(store):
    """Error:a spawned-by b, b spawned-by a → RelationCycleError(per type DFS)。"""
    store.add_relation("a", "b", RELATION_SPAWNED_BY)
    with pytest.raises(RelationCycleError, match="cycle"):
        store.add_relation("b", "a", RELATION_SPAWNED_BY)


def test_mixed_type_not_cycle(store):
    """Edge:A spawned-by B,B depends-on A → add 通过(per-type DFS 隔离)。"""
    store.add_relation("a", "b", RELATION_SPAWNED_BY)
    # depends-on 与 spawned-by 是独立 DAG,不算环
    rel = store.add_relation("b", "a", RELATION_DEPENDS_ON)
    assert rel.relation_type == RELATION_DEPENDS_ON


def test_cycle_three_node_chain(store):
    """3 节点环:a→b→c→a 同 type 检出。"""
    store.add_relation("a", "b", RELATION_DEPENDS_ON)
    store.add_relation("b", "c", RELATION_DEPENDS_ON)
    with pytest.raises(RelationCycleError):
        store.add_relation("c", "a", RELATION_DEPENDS_ON)


def test_self_loop_is_cycle(store):
    """src==dst 自环 → 环。"""
    with pytest.raises(RelationCycleError):
        store.add_relation("a", "a", RELATION_SPAWNED_BY)


def test_edit_relation_in_place_preserves_audit(store):
    """Happy/R2 edit:in-place 改 type + confidence,created_at/created_by 保留,
    updated_at 改。"""
    store.add_relation("a", "b", RELATION_DEPENDS_ON, confidence=0.4)
    before = store.get_relation("a", "b", RELATION_DEPENDS_ON)
    import time

    time.sleep(1.1)  # 跨秒确保 updated_at 变(秒级时间戳)
    edited = store.edit_relation(
        "a", "b", RELATION_SPAWNED_BY, confidence=0.8
    )
    assert edited.relation_type == RELATION_SPAWNED_BY
    assert edited.confidence == 0.8
    # audit trail 保留
    assert edited.created_at == before.created_at
    assert edited.created_by == before.created_by
    # updated_at 改
    assert edited.updated_at != before.updated_at
    # 旧 type 边消失
    assert store.get_relation("a", "b", RELATION_DEPENDS_ON) is None
    assert store.get_relation("a", "b", RELATION_SPAWNED_BY) is not None


def test_edit_relation_only_confidence_keeps_type(store):
    """edit 只改 confidence → type 不变,updated_at 改。"""
    store.add_relation("a", "b", RELATION_SPAWNED_BY, confidence=0.3)
    edited = store.edit_relation("a", "b", RELATION_SPAWNED_BY, confidence=0.95)
    assert edited.relation_type == RELATION_SPAWNED_BY
    assert edited.confidence == 0.95


def test_edit_relation_not_found_raises(store):
    """Error:edit_relation 目标不存在 → RelationNotFoundError。"""
    with pytest.raises(RelationNotFoundError, match="no relation"):
        store.edit_relation("a", "b", RELATION_SPAWNED_BY, confidence=0.8)


def test_edit_relation_rejects_unknown_type(store):
    store.add_relation("a", "b", RELATION_SPAWNED_BY)
    with pytest.raises(ValueError, match="unknown relation type"):
        store.edit_relation("a", "b", "blocks")


def test_edit_relation_to_cycle_raises(store):
    """edit 改 type 后在新 type DAG 成环 → raise。

    现有 spawned-by: b→a。edit a→b 改成 spawned-by 会构成 a→b + b→a 环。
    """
    store.add_relation("b", "a", RELATION_SPAWNED_BY)  # b spawned-by a
    store.add_relation("a", "b", RELATION_DEPENDS_ON)  # a depends-on b(另一 type)
    with pytest.raises(RelationCycleError):
        # 改 a→b 为 spawned-by → 与既有 b→a(spawned-by)成环
        store.edit_relation("a", "b", RELATION_SPAWNED_BY)


def test_remove_relation_all_types(store):
    store.add_relation("a", "b", RELATION_SPAWNED_BY)
    store.add_relation("a", "b", RELATION_DEPENDS_ON)
    n = store.remove_relation("a", "b")
    assert n == 2
    assert store.list_relations("a") == []


def test_remove_relation_specific_type(store):
    store.add_relation("a", "b", RELATION_SPAWNED_BY)
    store.add_relation("a", "b", RELATION_DEPENDS_ON)
    n = store.remove_relation("a", "b", RELATION_SPAWNED_BY)
    assert n == 1
    assert store.get_relation("a", "b", RELATION_SPAWNED_BY) is None
    assert store.get_relation("a", "b", RELATION_DEPENDS_ON) is not None


def test_remove_relation_no_match_returns_zero(store):
    assert store.remove_relation("x", "y") == 0


def test_list_relations_both_directions(store):
    """list_relations(task) 返回 src 或 dst 命中的全部边。"""
    store.add_relation("a", "b", RELATION_SPAWNED_BY)  # a 是 src
    store.add_relation("c", "a", RELATION_DEPENDS_ON)  # a 是 dst
    rels = store.list_relations("a")
    assert {r.relation_type for r in rels} == {RELATION_SPAWNED_BY, RELATION_DEPENDS_ON}


def test_list_relations_empty(store):
    assert store.list_relations("nobody") == []


def test_persistence_across_instances(store, tmp_path):
    """Integration:reopen RelationStore,relation 仍可读(KTD1 同库持久)。"""
    store.add_relation("a", "b", RELATION_SPAWNED_BY, confidence=0.6)
    store2 = RelationStore(tmp_path / "tasks.db")
    rel = store2.get_relation("a", "b", RELATION_SPAWNED_BY)
    assert rel is not None
    assert rel.confidence == 0.6
