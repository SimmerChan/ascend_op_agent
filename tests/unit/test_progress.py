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

"""U2 progress 聚合单测。fake CheckpointStore(dict-backed get_status + list_all_threads)。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ascend_op_agent.task_store import TASK_TYPE_DEVELOP, TaskStore
from ascend_op_agent.task_store.progress import get_task_progress, list_progress


@dataclass
class _PC:
    thread_id: str
    current_phase: str
    status: str
    updated_at: str = ""


class FakeCheckpointStore:
    """dict-backed,模拟 CheckpointStore.get_status + list_all_threads。"""

    def __init__(self, threads):
        # threads: {tid: (status, phase)}
        self._threads = threads

    def get_status(self, tid):
        t = self._threads.get(tid)
        return t[0] if t else None

    def list_all_threads(self):
        return [_PC(tid, phase, status) for tid, (status, phase) in self._threads.items()]


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_list_progress_states(store):
    store.create_task(TASK_TYPE_DEVELOP)
    tid2 = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid2, "t1")
    ck = FakeCheckpointStore({"t1": ("running", "codegen")})
    rows = list_progress(store, ck)
    assert len(rows) == 2
    by_id = {r["task_id"]: r for r in rows}
    # 无 thread 的 task = draft
    assert any(r["state"] == "draft" for r in rows)
    # 有 running thread 的 = running
    assert by_id[tid2]["state"] == "running"
    assert by_id[tid2]["thread_count"] == 1


def test_get_task_progress_state_phase_threads(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    ck = FakeCheckpointStore({"t1": ("done", "compile"), "t2": ("running", "precision")})
    prog = get_task_progress(tid, store, ck)
    assert prog["task_id"] == tid
    assert prog["state"] == "running"  # rollup single source
    assert prog["phase"] == "precision"  # running thread 的 phase
    assert len(prog["threads"]) == 2
    assert {th["thread_id"] for th in prog["threads"]} == {"t1", "t2"}
    # 一期-a shape:deferred 字段空
    assert prog["subtasks"] == []
    assert prog["artifacts"] == []


def test_get_task_progress_draft_no_threads(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    ck = FakeCheckpointStore({})
    prog = get_task_progress(tid, store, ck)
    assert prog["state"] == "draft"
    assert prog["phase"] is None
    assert prog["threads"] == []


def test_get_task_progress_phase_falls_back_to_latest_when_no_running(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    ck = FakeCheckpointStore({"t1": ("done", "delivery")})
    prog = get_task_progress(tid, store, ck)
    assert prog["state"] == "done"
    assert prog["phase"] == "delivery"  # 无 running → 最近 thread 的 phase


def test_get_task_progress_unknown_raises(store):
    ck = FakeCheckpointStore({})
    with pytest.raises(KeyError, match="unknown task"):
        get_task_progress("nope", store, ck)


def test_get_task_progress_thread_missing_in_checkpoint(store):
    # thread 在 task_threads 但 CheckpointStore 无记录 → status/phase None,不崩
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "ghost")
    ck = FakeCheckpointStore({})
    prog = get_task_progress(tid, store, ck)
    assert prog["threads"][0]["status"] is None
    assert prog["threads"][0]["phase"] is None
