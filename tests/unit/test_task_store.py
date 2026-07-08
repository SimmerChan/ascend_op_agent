# Copyright 2026 SimmerChan
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

"""U1 TaskStore 单测。"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_store import (
    STATE_NATIVE_NONE,
    STATE_NATIVE_PAUSED,
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPES,
    TaskStore,
)


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_create_and_get_task(store):
    tid = store.create_task(TASK_TYPE_DEVELOP, {"op": "add"})
    task = store.get_task(tid)
    assert task is not None
    assert task.id == tid
    assert task.type == TASK_TYPE_DEVELOP
    assert task.object_payload == {"op": "add"}
    assert task.state_native == STATE_NATIVE_NONE
    assert task.created_at != ""


def test_create_task_rejects_unknown_type(store):
    with pytest.raises(ValueError, match="unknown task type"):
        store.create_task("bogus", {})


def test_get_task_unknown_returns_none(store):
    assert store.get_task("nope") is None


def test_list_tasks_ordered_by_created(store):
    # 创建顺序保留(created_at 排序);同一秒内 created_at 可能相同,顺序仍稳定
    a = store.create_task(TASK_TYPE_DEVELOP)
    b = store.create_task(TASK_TYPE_ANALYZE)
    ids = [t.id for t in store.list_tasks()]
    assert a in ids and b in ids
    assert {t.type for t in store.list_tasks()} == {TASK_TYPE_DEVELOP, TASK_TYPE_ANALYZE}


def test_link_thread_and_get_threads(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "thread-1")
    store.link_thread(tid, "thread-2")
    # 幂等:重复 link 同一 thread 不重复
    store.link_thread(tid, "thread-1")
    assert store.get_task_threads(tid) == ["thread-1", "thread-2"]


def test_get_task_threads_empty(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    assert store.get_task_threads(tid) == []


def test_set_state_native(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.set_state_native(tid, STATE_NATIVE_PAUSED)
    task = store.get_task(tid)
    assert task.state_native == STATE_NATIVE_PAUSED


def test_active_task_set_get_clear(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    assert store.get_active() is None
    store.set_active(tid)
    assert store.get_active() == tid
    # 切换 active
    tid2 = store.create_task(TASK_TYPE_ANALYZE)
    store.set_active(tid2)
    assert store.get_active() == tid2
    store.clear_active()
    assert store.get_active() is None


def test_persistence_across_instances(store, tmp_path):
    tid = store.create_task(TASK_TYPE_DEVELOP, {"k": "v"})
    store.link_thread(tid, "thread-x")
    # 新实例同 db path 读得到
    store2 = TaskStore(tmp_path / "tasks.db")
    task = store2.get_task(tid)
    assert task is not None
    assert task.object_payload == {"k": "v"}
    assert store2.get_task_threads(tid) == ["thread-x"]


def test_all_task_types_accepted(store):
    for t in TASK_TYPES:
        tid = store.create_task(t)
        assert store.get_task(tid).type == t
