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

"""U4 显式命令单测。fake checkpoint_store + fake router。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ascend_op_agent.task_router.commands import NoActiveTaskError, TaskCommands
from ascend_op_agent.task_store import (
    STATE_NATIVE_NONE,
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPES,
    TaskStore,
)


@dataclass
class _PC:
    thread_id: str
    current_phase: str
    status: str
    updated_at: str = ""


class FakeCheckpointStore:
    def __init__(self, threads):
        self._threads = threads  # {tid: (status, phase)}

    def get_status(self, tid):
        t = self._threads.get(tid)
        return t[0] if t else None

    def list_all_threads(self):
        return [_PC(tid, phase, status) for tid, (status, phase) in self._threads.items()]


class FakeRouter:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, task_id, user_input, thread_id=None):
        self.dispatched.append((task_id, user_input, thread_id))
        return {"thread_id": thread_id or "th-1", "state": {"ok": True}}


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_new_creates_and_sets_active(store):
    ck = FakeCheckpointStore({})
    cmds = TaskCommands(store, checkpoint_store=ck)
    tid = cmds.new(TASK_TYPE_DEVELOP, {"op": "add"})
    assert store.get_task(tid).type == TASK_TYPE_DEVELOP
    assert store.get_active() == tid


def test_new_rejects_unknown_type(store):
    cmds = TaskCommands(store, checkpoint_store=FakeCheckpointStore({}))
    with pytest.raises(ValueError, match="unknown task type"):
        cmds.new("bogus")


def test_select_sets_active(store):
    cmds = TaskCommands(store, checkpoint_store=FakeCheckpointStore({}))
    tid = store.create_task(TASK_TYPE_DEVELOP)
    cmds.select(tid)
    assert store.get_active() == tid


def test_select_unknown_raises(store):
    cmds = TaskCommands(store, checkpoint_store=FakeCheckpointStore({}))
    with pytest.raises(KeyError, match="unknown task"):
        cmds.select("nope")


def test_list_requires_checkpoint_store(store):
    cmds = TaskCommands(store, checkpoint_store=None)
    with pytest.raises(RuntimeError, match="checkpoint_store required"):
        cmds.list()


def test_list_returns_progress(store):
    ck = FakeCheckpointStore({})
    cmds = TaskCommands(store, checkpoint_store=ck)
    cmds.new(TASK_TYPE_DEVELOP)
    rows = cmds.list()
    assert len(rows) == 1
    assert rows[0]["state"] == "draft"  # 无 thread


def test_progress_uses_active_when_no_id(store):
    ck = FakeCheckpointStore({})
    cmds = TaskCommands(store, checkpoint_store=ck)
    tid = cmds.new(TASK_TYPE_DEVELOP)
    prog = cmds.progress()
    assert prog["task_id"] == tid
    assert prog["state"] == "draft"


def test_progress_no_active_raises(store):
    ck = FakeCheckpointStore({})
    cmds = TaskCommands(store, checkpoint_store=ck)
    with pytest.raises(NoActiveTaskError):
        cmds.progress()


def test_progress_explicit_id(store):
    ck = FakeCheckpointStore({"t1": ("running", "codegen")})
    cmds = TaskCommands(store, checkpoint_store=ck)
    tid = store.create_task(TASK_TYPE_ANALYZE)
    store.link_thread(tid, "t1")
    prog = cmds.progress(task_id=tid)
    assert prog["state"] == "running"


def test_run_requires_router(store):
    cmds = TaskCommands(store, checkpoint_store=FakeCheckpointStore({}), router=None)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    with pytest.raises(RuntimeError, match="router required"):
        cmds.run(tid, "x")


def test_run_dispatches(store):
    router = FakeRouter()
    cmds = TaskCommands(store, checkpoint_store=FakeCheckpointStore({}), router=router)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    result = cmds.run(tid, "开发 add", thread_id="th-9")
    assert router.dispatched == [(tid, "开发 add", "th-9")]
    assert result["thread_id"] == "th-9"
