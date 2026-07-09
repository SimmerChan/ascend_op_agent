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

"""U6 上下文隔离单测(KTD5 兜底 + KTD11 跨 db 读)。

覆盖 plan U6 Test scenarios 的隔离 / 跨 db 部分(跨任务读受控部分在
``test_cross_task_read.py``):
- Happy(AE11):active migrate 执行,analyze task 的 context 不混入 migrate 上下文
- Edge:任务侧 thread_id filter 排除非本 task 的 thread(KTD11)
- Edge:无 depends-on → 跨任务读拒绝(返回空 + audit log,不崩)
- Error:artifact 不存在 → 读返回空(不崩)
- Edge(KTD11):CheckpointStore list_all_threads()(shipped 无参)跨 db 读,WAL busy
  不挂死(< 30s timeout);filter 在任务侧做,不污染 CheckpointStore
- **byte-identical 回归**(KTD1):``list_all_threads`` 签名无参;``list_pending`` /
  ``save`` 输出格式 + 字段不变;CheckpointStore 完全不动
"""

from __future__ import annotations

import inspect
import logging
import threading

import pytest

from ascend_op_agent.orchestrator.checkpoint import (
    CheckpointStore,
    PendingCheckpoint,
    STATUS_DONE,
    STATUS_RUNNING,
)
from ascend_op_agent.task_router.context_scope import ContextScope
from ascend_op_agent.task_store.artifacts import (
    ARTIFACT_TYPE_REPORT,
    ArtifactStore,
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
def ck_path(tmp_path):
    return tmp_path / "checkpoints.db"


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
def checkpoint_store(ck_path):
    return CheckpointStore(ck_path)


@pytest.fixture
def scope(task_store, relation_store, artifact_store):
    return ContextScope(task_store, relation_store, artifact_store)


# ---- KTD5 task-scoped 隔离(AE11) ----


def test_isolation_filters_threads_to_active_task(scope, task_store, checkpoint_store):
    """AE11:active migrate 执行,analyze task 的 thread 不混入 migrate 上下文。

    migrate task 绑 t1/t2,analyze task 绑 t3。CheckpointStore 全部 thread 记录
    (list_all_threads shipped 无参)经任务侧 filter 后,migrate 只见 t1/t2。
    """
    migrate = task_store.create_task("migrate")
    analyze = task_store.create_task("analyze")
    task_store.link_thread(migrate, "t1")
    task_store.link_thread(migrate, "t2")
    task_store.link_thread(analyze, "t3")
    # CheckpointStore 写 3 个 thread 记录(跨 db)
    checkpoint_store.save("t1", {"phase": "compile"}, status=STATUS_RUNNING)
    checkpoint_store.save("t2", {"phase": "precision"}, status=STATUS_RUNNING)
    checkpoint_store.save("t3", {"phase": "analyze"}, status=STATUS_RUNNING)

    task_store.set_active(migrate)
    records = scope.list_task_checkpoint_records(migrate, checkpoint_store)
    tids = {r.thread_id for r in records}
    assert tids == {"t1", "t2"}  # analyze 的 t3 被隔离掉
    assert "t3" not in tids  # AE11:不混入


def test_filter_threads_excludes_foreign_threads(scope, task_store):
    """Edge:任务侧 filter 排除非本 task 的 thread(直接 filter list_all_threads 输出)。"""
    migrate = task_store.create_task("migrate")
    task_store.link_thread(migrate, "t1")
    task_store.link_thread(migrate, "t2")

    fake_all = [
        PendingCheckpoint(thread_id="t1", current_phase="a", status="running",
                          updated_at="2026"),
        PendingCheckpoint(thread_id="t2", current_phase="b", status="running",
                          updated_at="2026"),
        PendingCheckpoint(thread_id="t3", current_phase="c", status="running",
                          updated_at="2026"),  # foreign
        PendingCheckpoint(thread_id="t4", current_phase="d", status="running",
                          updated_at="2026"),  # foreign
    ]
    filtered = scope.filter_threads(migrate, fake_all)
    assert {f.thread_id for f in filtered} == {"t1", "t2"}


def test_task_thread_ids_set(scope, task_store):
    task_store.create_task_migrate = lambda: None  # noqa: placeholder
    migrate = task_store.create_task("migrate")
    task_store.link_thread(migrate, "t1")
    task_store.link_thread(migrate, "t2")
    task_store.link_thread(migrate, "t1")  # 幂等
    assert scope.task_thread_ids(migrate) == {"t1", "t2"}


def test_task_thread_ids_empty_task(scope, task_store):
    migrate = task_store.create_task("migrate")
    assert scope.task_thread_ids(migrate) == set()
    assert scope.filter_threads(migrate, [
        PendingCheckpoint("t9", None, "running", "2026")]) == []


# ---- KTD10/R10 跨任务读拒绝(无 depends-on) ----


def test_cross_task_read_rejected_no_depends_on(scope, task_store, relation_store,
                                                 caplog):
    """Edge:active task 无 depends-on → 跨任务读拒绝(返回空 + audit log,不崩)。"""
    reader = task_store.create_task("analyze")
    with caplog.at_level(logging.INFO):
        arts = scope.read_related_artifacts(reader)
    assert arts == []
    assert "no depends-on edge" in caplog.text


def test_cross_task_read_spawned_by_does_not_grant_read(scope, task_store,
                                                        relation_store, caplog):
    """R10:spawned-by 边无读权限(仅 depends-on 授权)。"""
    reader = task_store.create_task("analyze")
    src = task_store.create_task("migrate")
    relation_store.add_relation(reader, src, RELATION_SPAWNED_BY)
    with caplog.at_level(logging.INFO):
        arts = scope.read_related_artifacts(reader)
    assert arts == []  # spawned-by 不授权
    assert "no depends-on edge" in caplog.text


def test_cross_task_read_artifact_missing_returns_empty_not_crash(
    scope, task_store, relation_store
):
    """Error:reader depends-on src,但 src 无 artifact → 读返回空(不崩)。"""
    reader = task_store.create_task("analyze")
    src = task_store.create_task("migrate")
    relation_store.add_relation(reader, src, RELATION_DEPENDS_ON)
    # src 没标任何产物
    assert scope.read_related_artifacts(reader) == []


def test_cross_task_read_unknown_reader_no_relations_no_crash(scope):
    """Error:reader task_id 无任何关系 → 空(不崩,不 raise)。"""
    assert scope.read_related_artifacts("ghost-task") == []


# ---- KTD11 跨 db 读:CheckpointStore 不污染 + busy 不挂死 ----


def test_list_all_threads_called_readonly_no_mutation(scope, task_store,
                                                      checkpoint_store):
    """KTD11:ContextScope 只 read-only 调 list_all_threads,不改 CheckpointStore。"""
    checkpoint_store.save("t1", {"p": 1}, status=STATUS_RUNNING)
    before = checkpoint_store.list_all_threads()
    _ = scope.list_task_checkpoint_records("any", checkpoint_store)
    after = checkpoint_store.list_all_threads()
    assert before == after  # read-only,无副作用


def test_cross_db_read_not_hang_under_concurrent_writes(scope, task_store,
                                                         checkpoint_store):
    """KTD11:WAL busy 时不挂死(< 30s timeout)。

    一个线程持续写 CheckpointStore(BEGIN IMMEDIATE),主线程并发调
    list_all_threads() 跨 db 读 —— 因 busy_timeout=30000 + WAL,读应快速返回
    不挂死。
    """
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        try:
            while not stop.is_set() and i < 50:
                checkpoint_store.save(f"w{i}", {"i": i}, status=STATUS_RUNNING)
                i += 1
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=writer)
    t.start()
    try:
        # 并发读(list_all_threads shipped 无参)
        results = []
        for _ in range(20):
            results.append(checkpoint_store.list_all_threads())
        assert all(isinstance(r, list) for r in results)
    finally:
        stop.set()
        t.join(timeout=10)
    assert not errors


# ---- byte-identical 回归(KTD1) ----


def test_list_all_threads_signature_no_params():
    """KTD11 守门:list_all_threads 无参(只 self)—— 不被改成带 task_id 签名。"""
    sig = inspect.signature(CheckpointStore.list_all_threads)
    params = list(sig.parameters.keys())
    assert params == ["self"], (
        f"list_all_threads must remain parameterless (self only); got {params}"
    )


def test_checkpoint_store_methods_signatures_unchanged():
    """KTD1 byte-identical:list_pending / save / resume(load) 签名不变。"""
    sig_lp = inspect.signature(CheckpointStore.list_pending)
    assert list(sig_lp.parameters.keys()) == ["self"]
    sig_save = inspect.signature(CheckpointStore.save)
    # save(self, thread_id, state, current_phase=None, status=..., schema_version=...)
    save_params = list(sig_save.parameters.keys())
    assert save_params[0] == "self"
    assert save_params[1] == "thread_id"
    assert "status" in save_params
    assert "schema_version" in save_params


def test_list_pending_output_format_unchanged(checkpoint_store):
    """KTD1 byte-identical:list_pending 返回 PendingCheckpoint,字段顺序 + 值不变。

    对照一期-a baseline:PendingCheckpoint(thread_id, current_phase, status,
    updated_at);status==done 不出现在 list_pending。
    """
    checkpoint_store.save("t1", {"phase": "a"}, current_phase="a",
                          status=STATUS_RUNNING)
    checkpoint_store.save("t2", {"phase": "b"}, current_phase="b",
                          status=STATUS_DONE)
    pending = checkpoint_store.list_pending()
    assert all(isinstance(p, PendingCheckpoint) for p in pending)
    # done 被排除
    assert {p.thread_id for p in pending} == {"t1"}
    p0 = pending[0]
    assert p0.thread_id == "t1"
    assert p0.current_phase == "a"
    assert p0.status == STATUS_RUNNING
    # 字段顺序 byte-identical
    assert list(p0.__dataclass_fields__.keys()) == [
        "thread_id", "current_phase", "status", "updated_at"
    ]


def test_list_all_threads_includes_done(checkpoint_store):
    """list_all_threads(shipped 无参)与 list_pending 同 shape 但含 done。"""
    checkpoint_store.save("t1", {"phase": "a"}, status=STATUS_RUNNING)
    checkpoint_store.save("t2", {"phase": "b"}, status=STATUS_DONE)
    all_threads = checkpoint_store.list_all_threads()
    assert {p.thread_id for p in all_threads} == {"t1", "t2"}
    assert all(isinstance(p, PendingCheckpoint) for p in all_threads)


def test_save_then_load_roundtrip_unchanged(checkpoint_store):
    """KTD1 byte-identical:save / load 语义不变。"""
    checkpoint_store.save("tx", {"k": "v", "nested": [1, 2]}, status=STATUS_RUNNING)
    loaded = checkpoint_store.load("tx")
    assert loaded == {"k": "v", "nested": [1, 2]}
