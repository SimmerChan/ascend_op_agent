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

"""U6 受控跨任务读集成测试(KTD10 + R10;AE4)。

覆盖 plan U6 Integration / Edge / Error 场景 —— reader(analyze)depends-on
src(migrate)→ 读 src 标过的 artifact 路径(只读 artifacts,不复制 src context,
不读 src 的 memory/对话)。writer 用 MockArtifactWriter(验证"mock 不影响跨 task
读逻辑")。
"""

from __future__ import annotations

import logging

import pytest

from ascend_op_agent.task_router.context_scope import ContextScope
from ascend_op_agent.task_store.artifacts import (
    ARTIFACT_TYPE_LOG,
    ARTIFACT_TYPE_REPORT,
    ARTIFACT_TYPE_SCRIPT,
    WRITTEN_BY_EXECUTOR,
    ArtifactStore,
    MockArtifactWriter,
    make_writer,
)
from ascend_op_agent.task_store.relations import (
    RELATION_DEPENDS_ON,
    RELATION_SPAWNED_BY,
    RelationStore,
)
from ascend_op_agent.task_store.store import TaskStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "tasks.db"


@pytest.fixture
def task_store(db_path):
    return TaskStore(db_path)


@pytest.fixture
def relation_store(db_path):
    return RelationStore(db_path)


@pytest.fixture
def artifact_store(db_path):
    return ArtifactStore(db_path)


@pytest.fixture
def scope(task_store, relation_store, artifact_store):
    return ContextScope(task_store, relation_store, artifact_store)


def test_cross_task_read_depends_on_reads_artifacts(scope, task_store,
                                                    relation_store, artifact_store):
    """AE4 happy:analyze depends-on migrate → analyze 读 migrate 标过的产物。

    只读 artifacts 路径注册表,不读 migrate 的 memory/对话(KTD5 隔离域)。
    """
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)

    # migrate 的 writer hook 落产物路径(U8 executor 在 dispatch boundary 写;
    # 此处用 make_writer 模拟 U8 executor 集成)
    writer = make_writer(artifact_store, written_by=WRITTEN_BY_EXECUTOR)
    writer(migrate, ARTIFACT_TYPE_REPORT, "/runs/migrate/report.md")
    writer(migrate, ARTIFACT_TYPE_SCRIPT, "/runs/migrate/adapt.py")

    arts = scope.read_related_artifacts(analyze)
    paths = {a.path for a in arts}
    assert paths == {"/runs/migrate/report.md", "/runs/migrate/adapt.py"}
    # 全部指向 migrate(权限域正确)
    assert all(a.task_id == migrate for a in arts)


def test_cross_task_read_does_not_copy_src_context(scope, task_store,
                                                   relation_store, artifact_store):
    """AE4:受控读 = 只读 artifact 路径,不读 src 的 memory/对话。

    migrate 绑 thread t_src,analyze 绑 thread t_reader。跨任务读结果只含
    artifact 路径,不含 src 的 thread / checkpoint context。
    """
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)
    task_store.link_thread(migrate, "t_src")
    task_store.link_thread(analyze, "t_reader")

    artifact_store.write(migrate, ARTIFACT_TYPE_REPORT, "/runs/r.md", "executor")

    arts = scope.read_related_artifacts(analyze)
    assert len(arts) == 1
    # 结果是 Artifact(路径),不是 thread context
    assert not hasattr(arts[0], "thread_id")
    assert arts[0].path == "/runs/r.md"


def test_cross_task_read_by_type(scope, task_store, relation_store, artifact_store):
    """read_related_artifacts_by_type 只取指定类型。"""
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)
    artifact_store.write(migrate, ARTIFACT_TYPE_REPORT, "/r.md")
    artifact_store.write(migrate, ARTIFACT_TYPE_SCRIPT, "/s.py")
    artifact_store.write(migrate, ARTIFACT_TYPE_LOG, "/l.log")

    reports = scope.read_related_artifacts_by_type(analyze, ARTIFACT_TYPE_REPORT)
    assert len(reports) == 1
    assert reports[0].path == "/r.md"
    assert reports[0].artifact_type == ARTIFACT_TYPE_REPORT


def test_cross_task_read_mock_writer_forward(scope, task_store, relation_store,
                                             artifact_store):
    """mock writer forward_store 落库后可被读(mock 不影响跨 task 读逻辑)。"""
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)

    mock = MockArtifactWriter(forward_store=artifact_store)
    mock(migrate, ARTIFACT_TYPE_REPORT, "/mock/r.md")
    assert len(mock.calls) == 1

    arts = scope.read_related_artifacts(analyze)
    assert len(arts) == 1
    assert arts[0].path == "/mock/r.md"


def test_cross_task_read_mock_writer_no_forward_empty(scope, task_store,
                                                      relation_store, artifact_store):
    """mock writer 不 forward → 跨任务读见空(注册表无落库)。证明读的是真注册表,
    不是 mock 的内存记录。"""
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)

    mock = MockArtifactWriter()  # 不 forward
    mock(migrate, ARTIFACT_TYPE_REPORT, "/mock/r.md")

    assert scope.read_related_artifacts(analyze) == []


def test_cross_task_read_rejected_when_only_spawned_by(scope, task_store,
                                                       relation_store, artifact_store,
                                                       caplog):
    """Edge:reader 与 src 仅有 spawned-by 边 → 跨任务读拒绝(返回空 + audit log)。

    R10:只有 depends-on 授权读;spawned-by 不授权。
    """
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_SPAWNED_BY)
    artifact_store.write(migrate, ARTIFACT_TYPE_REPORT, "/r.md")

    with caplog.at_level(logging.INFO):
        arts = scope.read_related_artifacts(analyze)
    assert arts == []
    assert "no depends-on edge" in caplog.text


def test_cross_task_read_rejected_reverse_depends_on(scope, task_store,
                                                     relation_store, artifact_store):
    """Edge:边方向相反(migrate depends-on analyze)→ analyze 无权读 migrate。

    depends-on 的 dst 是产物源,reader 必须是 src。reader 作 dst 时不授权。
    """
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    # migrate depends-on analyze(reader 作 dst,不授权)
    relation_store.add_relation(migrate, analyze, RELATION_DEPENDS_ON)
    artifact_store.write(analyze, ARTIFACT_TYPE_REPORT, "/analyze/r.md")

    # analyze 作 reader:它没有"作为 src 的 depends-on 边" → 读不到
    assert scope.read_related_artifacts(analyze) == []


def test_cross_task_read_no_artifact_registered_returns_empty(scope, task_store,
                                                              relation_store):
    """Error:depends-on 存在但 src 没标 artifact → 读返回空(不崩)。"""
    analyze = task_store.create_task("analyze")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(analyze, migrate, RELATION_DEPENDS_ON)
    assert scope.read_related_artifacts(analyze) == []


def test_cross_task_read_multiple_depends_on_sources(scope, task_store,
                                                     relation_store, artifact_store):
    """reader depends-on 多个 src → 读全部 src 的产物合并。"""
    optimize = task_store.create_task("optimize")
    src_a = task_store.create_task("analyze")
    src_b = task_store.create_task("migrate")
    relation_store.add_relation(optimize, src_a, RELATION_DEPENDS_ON)
    relation_store.add_relation(optimize, src_b, RELATION_DEPENDS_ON)
    artifact_store.write(src_a, ARTIFACT_TYPE_REPORT, "/a.md")
    artifact_store.write(src_b, ARTIFACT_TYPE_REPORT, "/b.md")

    arts = scope.read_related_artifacts(optimize)
    assert {a.path for a in arts} == {"/a.md", "/b.md"}


def test_cross_task_read_does_not_leak_other_readers(scope, task_store,
                                                    relation_store, artifact_store):
    """reader A depends-on src → reader B(无 depends-on)读不到。权限按 reader 隔离。"""
    reader_a = task_store.create_task("analyze")
    reader_b = task_store.create_task("optimize")
    migrate = task_store.create_task("migrate")
    relation_store.add_relation(reader_a, migrate, RELATION_DEPENDS_ON)
    artifact_store.write(migrate, ARTIFACT_TYPE_REPORT, "/r.md")

    assert len(scope.read_related_artifacts(reader_a)) == 1
    assert scope.read_related_artifacts(reader_b) == []  # B 无 depends-on
