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

"""U5 relation lifecycle 集成测试。

端到端走完整 AE3 链路:suggest(不落库)→ confirm(link 落库)→ edit(in-place)
→ 跨 session 持久化。验证多层协作(TaskStore + RelationStore + RelationBuilder +
TaskCommands),不依赖真 LLM(mock LLM callable)。
"""

from __future__ import annotations

import json

from ascend_op_agent.task_router.commands import TaskCommands
from ascend_op_agent.task_router.relation_builder import RelationBuilder
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
    TaskStore,
)
from ascend_op_agent.task_store.relations import (
    RELATION_DEPENDS_ON,
    RELATION_SPAWNED_BY,
    RelationStore,
)


def test_ae3_full_lifecycle_suggest_confirm_edit_persist(tmp_path):
    """AE3 + R2 edit + 跨 session 持久化 的完整生命周期。

    场景:migrate task 执行中 → 新建 analyze task → LLM suggest spawned-by migrate
    → 用户 confirm(link)落库 → edit 改成 depends-on + 调 confidence → 重开 db
    仍可读。
    """
    db = tmp_path / "tasks.db"
    store = TaskStore(db)

    # 1. 建 migrate(active)+ analyze task
    migrate_id = store.create_task(TASK_TYPE_MIGRATE, {"repo": "resnet50", "entry": "train.py"})
    store.set_active(migrate_id)
    analyze_id = store.create_task(
        TASK_TYPE_ANALYZE, {"op": "matmul", "profiling": "ascend_prof"}
    )

    # 2. mock LLM suggest spawned-by migrate(AE3)
    def llm(prompt: str) -> str:
        return json.dumps(
            {
                "relations": [
                    {
                        "relation_type": "spawned-by",
                        "confidence": 0.92,
                        "rationale": "analyze 派生自迁移中发现的瓶颈 op",
                    }
                ]
            }
        )

    cmds = TaskCommands(store)
    suggestions = cmds.suggest(new_task_id=analyze_id, llm_call=llm)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.relation_type == RELATION_SPAWNED_BY
    assert s.dst_task_id == migrate_id
    # suggest 不落库
    assert cmds.relations.list_relations(analyze_id) == []

    # 3. confirm:用户 link 落库(AE3 confirm)
    cmds.link(
        s.src_task_id, s.dst_task_id, s.relation_type, confidence=s.confidence
    )
    rel = cmds.relations.get_relation(analyze_id, migrate_id, RELATION_SPAWNED_BY)
    assert rel is not None
    assert rel.confidence == 0.92
    assert rel.created_by == "manual"

    # 4. AE6:手动再加一条 optimize depends-on analyze(独立 DAG,不环)
    optimize_id = store.create_task(TASK_TYPE_OPTIMIZE, {"target": "matmul"})
    cmds.link(optimize_id, analyze_id, RELATION_DEPENDS_ON, confidence=0.7)
    assert (
        cmds.relations.get_relation(optimize_id, analyze_id, RELATION_DEPENDS_ON)
        is not None
    )

    # 5. R2 edit:把 spawned-by in-place 改成 depends-on + 调 confidence
    edited = cmds.edit_relation(
        analyze_id, migrate_id, RELATION_DEPENDS_ON, confidence=0.85
    )
    assert edited.relation_type == RELATION_DEPENDS_ON
    assert edited.confidence == 0.85
    # audit trail:created_at 保留(KTD9 audit)
    original = cmds.relations.get_relation(analyze_id, migrate_id, RELATION_DEPENDS_ON)
    assert original is not None
    assert original.created_at == rel.created_at
    # 旧 spawned-by 边已不在
    assert (
        cmds.relations.get_relation(analyze_id, migrate_id, RELATION_SPAWNED_BY) is None
    )

    # 6. 跨 session 持久化:重开 TaskStore + RelationStore,关系仍可读
    store2 = TaskStore(db)
    cmds2 = TaskCommands(store2)
    # analyze_id 是两条边的端点(src of analyze→migrate,dst of optimize→analyze)
    rels = cmds2.relations.list_relations(analyze_id)
    rel_types = {(r.src_task_id, r.dst_task_id, r.relation_type) for r in rels}
    assert (analyze_id, migrate_id, RELATION_DEPENDS_ON) in rel_types
    assert (optimize_id, analyze_id, RELATION_DEPENDS_ON) in rel_types

    # 7. unlink 清理
    n1 = cmds2.unlink(analyze_id, migrate_id)
    n2 = cmds2.unlink(optimize_id, analyze_id)
    assert n1 == 1
    assert n2 == 1
    assert cmds2.relations.list_relations(analyze_id) == []


def test_relation_builder_with_commands_integration(tmp_path):
    """RelationBuilder 直注 + TaskCommands 落库 的 suggest→confirm 集成。"""
    store = TaskStore(tmp_path / "tasks.db")
    dev_id = store.create_task(TASK_TYPE_DEVELOP, {"op": "add"})
    store.set_active(dev_id)
    analyze_id = store.create_task(TASK_TYPE_ANALYZE, {"op": "add", "from": dev_id})

    builder = RelationBuilder(
        store,
        llm_call=lambda p: json.dumps(
            {"relations": [{"relation_type": "spawned-by", "confidence": 0.7}]}
        ),
    )
    suggestions = builder.suggest(analyze_id)
    assert len(suggestions) == 1

    # confirm via commands
    cmds = TaskCommands(store, relation_builder=builder)
    cmds.link(
        suggestions[0].src_task_id,
        suggestions[0].dst_task_id,
        suggestions[0].relation_type,
    )
    assert cmds.relations.get_relation(analyze_id, dev_id, RELATION_SPAWNED_BY) is not None


def test_relations_and_tasks_share_same_db(tmp_path):
    """KTD1:RelationStore 与 TaskStore 同库同文件(WAL 共享)。"""
    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    rel_store = RelationStore(db)
    assert store.db_path == rel_store.db_path
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_DEVELOP)
    rel_store.add_relation(a, b, RELATION_DEPENDS_ON)
    # 两个 store 读写同一 db
    assert rel_store.get_relation(a, b, RELATION_DEPENDS_ON) is not None
