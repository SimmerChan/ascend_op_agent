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

"""U2: CheckpointStore.list_all_threads(additive read-only)+ list_pending 回归(F2)。

F2 守门:list_pending 在新方法落地后 byte-identical(仍过滤 done)。
"""

from __future__ import annotations

import pytest

from ascend_op_agent.orchestrator.checkpoint import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    CheckpointStore,
)


@pytest.fixture
def store(tmp_path):
    return CheckpointStore(tmp_path / "ck.db")


def _save(store, tid, status, phase="p"):
    store.save(tid, {"x": 1}, current_phase=phase, status=status)


def test_list_all_threads_returns_all_including_done(store):
    _save(store, "t1", STATUS_RUNNING, "codegen")
    _save(store, "t2", STATUS_DONE, "delivery")
    _save(store, "t3", STATUS_FAILED, "precision")
    all_threads = store.list_all_threads()
    ids = {pc.thread_id for pc in all_threads}
    assert ids == {"t1", "t2", "t3"}  # 含 done


def test_list_pending_still_excludes_done_regression(store):
    """F2 回归:list_pending 仍过滤 done(byte-identical 行为)。"""
    _save(store, "t1", STATUS_PENDING, "a")
    _save(store, "t2", STATUS_DONE, "b")
    _save(store, "t3", STATUS_RUNNING, "c")
    _save(store, "t4", STATUS_FAILED, "d")
    pending = store.list_pending()
    pending_ids = {pc.thread_id for pc in pending}
    assert pending_ids == {"t1", "t3", "t4"}  # 不含 done(t2)
    assert "t2" not in pending_ids


def test_list_all_threads_shape_matches_pending(store):
    _save(store, "t1", STATUS_RUNNING, "codegen")
    all_pc = store.list_all_threads()
    pending_pc = store.list_pending()
    # 同 shape(PendingCheckpoint 字段集)
    assert all(hasattr(pc, "thread_id") and hasattr(pc, "status") for pc in all_pc)
    # list_all_threads 是 list_pending 的超集(含 done)
    assert {pc.thread_id for pc in pending_pc}.issubset({pc.thread_id for pc in all_pc})


def test_list_all_threads_empty(store):
    assert store.list_all_threads() == []
