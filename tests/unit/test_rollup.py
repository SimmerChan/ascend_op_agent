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

"""U1 R15 rollup 单测(cross-db 读协议 F1)。

thread_status_fn 注入(dict-backed),模拟调用方读 CheckpointStore。
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_store import (
    STATE_DONE,
    STATE_DRAFT,
    STATE_FAILED,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_NATIVE_NONE,
    STATE_NATIVE_PAUSED,
    TASK_TYPE_DEVELOP,
    THREAD_DONE,
    THREAD_FAILED,
    THREAD_PENDING,
    THREAD_RUNNING,
    THREAD_WAITING_CONFIRM,
    TaskStore,
    rollup_task_state,
)


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def _fn(status_map):
    """dict-backed thread_status_fn(模拟读 CheckpointStore)。"""
    return lambda tid: status_map.get(tid)


def test_rollup_draft_no_threads(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    assert rollup_task_state(tid, store, _fn({})) == STATE_DRAFT


def test_rollup_paused_no_threads(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.set_state_native(tid, STATE_NATIVE_PAUSED)
    assert rollup_task_state(tid, store, _fn({})) == STATE_PAUSED


def test_rollup_running_if_any_thread_active(store):
    # AE12: task 跨 3 thread,各 status,running 优先
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    store.link_thread(tid, "t3")
    status_map = {"t1": THREAD_DONE, "t2": THREAD_FAILED, "t3": THREAD_RUNNING}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_RUNNING


def test_rollup_priority_running_over_failed(store):
    # R15 priority:running > waiting_confirm > failed
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    status_map = {"t1": THREAD_FAILED, "t2": THREAD_WAITING_CONFIRM}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_RUNNING


def test_rollup_failed_when_no_running_but_failed_present(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    status_map = {"t1": THREAD_DONE, "t2": THREAD_FAILED}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_FAILED


def test_rollup_done_when_all_done(store):
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    status_map = {"t1": THREAD_DONE, "t2": THREAD_DONE}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_DONE


def test_rollup_failed_over_paused(store):
    # failed > paused(thread failed 时,即使 task 标 paused,failed 优先)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.set_state_native(tid, STATE_NATIVE_PAUSED)
    store.link_thread(tid, "t1")
    status_map = {"t1": THREAD_FAILED}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_FAILED


def test_rollup_paused_when_threads_pending_and_user_paused(store):
    # threads 全 pending(无 running/failed/done)+ 用户标 paused → paused
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.set_state_native(tid, STATE_NATIVE_PAUSED)
    store.link_thread(tid, "t1")
    status_map = {"t1": THREAD_PENDING}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_PAUSED


def test_rollup_running_when_threads_pending_not_paused(store):
    # threads 全 pending,未标 paused → task in-flight → running
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    status_map = {"t1": THREAD_PENDING}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_RUNNING


def test_rollup_thread_missing_status_best_effort(store, caplog):
    # F1 / Error:thread 在 task_threads 但 CheckpointStore 无记录(fn 返 None)
    # → best-effort,不崩(warn),residual → running(task in-flight)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "ghost")
    status_map = {}  # ghost 不在
    with caplog.at_level("WARNING"):
        state = rollup_task_state(tid, store, _fn(status_map))
    assert state == STATE_RUNNING  # 不崩;None 不算 done/residual → running
    assert "inconsistent" in caplog.text


def test_rollup_cross_db_live_reflects_mutation(store):
    # F1 cross-db 读协议:fn 读 live CheckpointStore 状态;mutate 后 rollup 反映新值
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    status_map = {"t1": THREAD_RUNNING}
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_RUNNING
    # 模拟 PhaseRunner 把 thread 推进到 done
    status_map["t1"] = THREAD_DONE
    assert rollup_task_state(tid, store, _fn(status_map)) == STATE_DONE


def test_rollup_unknown_task_raises(store):
    with pytest.raises(KeyError, match="unknown task"):
        rollup_task_state("nope", store, _fn({}))


def test_rollup_none_does_not_false_done(store):
    # None + done 混合:不误判 done(全 done 才 done)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    store.link_thread(tid, "t1")
    store.link_thread(tid, "t2")
    status_map = {"t1": THREAD_DONE}  # t2 = None
    assert rollup_task_state(tid, store, _fn(status_map)) != STATE_DONE
