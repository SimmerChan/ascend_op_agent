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

"""R8 progress-query 聚合(net-new,sit above list_pending,U2)。

**single source** = R15 rollup —— progress 读 rollup 结果,不重算 state。
一期-a progress 不含 artifact index(随 U6/U8 writer 落)+ subtasks(一期-a 无
relations,R2 deferred)。

thread_status_fn = ``CheckpointStore.get_status``(read-only,thread status 权威,
PhaseRunner 写)。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ascend_op_agent.task_store.models import THREAD_RUNNING, THREAD_WAITING_CONFIRM
from ascend_op_agent.task_store.rollup import rollup_task_state
from ascend_op_agent.task_store.store import TaskStore


def list_progress(store: TaskStore, checkpoint_store) -> List[Dict[str, Any]]:
    """列任务 + 每任务 state(rollup single source)+ thread 数(R8)。"""
    fn = checkpoint_store.get_status
    out: List[Dict[str, Any]] = []
    for t in store.list_tasks():
        state = rollup_task_state(t.id, store, fn)
        out.append(
            {
                "task_id": t.id,
                "type": t.type,
                "state": state,
                "thread_count": len(store.get_task_threads(t.id)),
            }
        )
    return out


def get_task_progress(task_id: str, store: TaskStore, checkpoint_store) -> Dict[str, Any]:
    """单任务进展:state(rollup single source)+ phase + threads + subtasks + artifacts。

    一期-a:subtasks/artifacts 空(relations/artifacts_index deferred)。
    phase = running/waiting_confirm thread 的 current_phase,否则最近 thread 的 phase。
    """
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")

    state = rollup_task_state(task_id, store, checkpoint_store.get_status)
    thread_ids = store.get_task_threads(task_id)
    all_threads = {pc.thread_id: pc for pc in checkpoint_store.list_all_threads()}

    threads: List[Dict[str, Any]] = []
    for tid in thread_ids:
        pc = all_threads.get(tid)
        threads.append(
            {
                "thread_id": tid,
                "status": pc.status if pc is not None else None,
                "phase": pc.current_phase if pc is not None else None,
            }
        )

    running = [th for th in threads if th["status"] in (THREAD_RUNNING, THREAD_WAITING_CONFIRM)]
    phase = running[0]["phase"] if running else (threads[-1]["phase"] if threads else None)

    return {
        "task_id": task_id,
        "type": task.type,
        "state": state,
        "phase": phase,
        "threads": threads,
        # 一期-a:deferred(R2 relations / U6-U8 artifact writer 未落)
        "subtasks": [],
        "artifacts": [],
    }
