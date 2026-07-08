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

"""R15 task state rollup(KTD3)。

state 分两类:
  - **rollup 自 thread states**:running / done / failed
  - **task-layer-native**:draft(无 thread)/ paused(用户标志)

混合优先级 ``running > waiting_confirm > failed > paused > done``。

cross-db 读协议(F1):``thread_status_fn`` 注入 —— 调用方读 CheckpointStore
(thread status 权威,PhaseRunner 写);rollup 先取 task_threads 再逐 thread 调 fn
取 live status。best-effort eventually-consistent:**永不持久化为 source of truth**
(只实时计算)。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from ascend_op_agent.task_store.models import (
    STATE_DONE,
    STATE_DRAFT,
    STATE_FAILED,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_NATIVE_PAUSED,
    THREAD_DONE,
    THREAD_FAILED,
    THREAD_RUNNING,
    THREAD_WAITING_CONFIRM,
)
from ascend_op_agent.task_store.store import TaskStore

logger = logging.getLogger(__name__)

#: 调用方注入:thread_id → thread status(读 CheckpointStore);None = thread 无记录
ThreadStatusFn = Callable[[str], Optional[str]]


def rollup_task_state(task_id: str, store: TaskStore, thread_status_fn: ThreadStatusFn) -> str:
    """推导 task 对外状态(R15)。

    Args:
        task_id: 任务 id。
        store: TaskStore(取 task + task_threads)。
        thread_status_fn: thread_id → thread status(调用方读 CheckpointStore)。

    Returns:
        R15 对外状态(draft / running / paused / done / failed)。

    Raises:
        KeyError: task_id 不存在。
    """
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(f"unknown task: {task_id}")

    thread_ids = store.get_task_threads(task_id)
    if not thread_ids:
        # task-layer-native:无 thread → draft(或 paused 若用户标)
        return STATE_PAUSED if task.state_native == STATE_NATIVE_PAUSED else STATE_DRAFT

    statuses: list[Optional[str]] = []
    for tid in thread_ids:
        s = thread_status_fn(tid)
        if s is None:
            logger.warning(
                "rollup: thread %s has no status in checkpoint store (inconsistent)", tid
            )
        statuses.append(s)

    # R15 priority: running > waiting_confirm > failed > paused > done
    if any(s in (THREAD_RUNNING, THREAD_WAITING_CONFIRM) for s in statuses):
        return STATE_RUNNING
    if any(s == THREAD_FAILED for s in statuses):
        return STATE_FAILED
    if task.state_native == STATE_NATIVE_PAUSED:
        return STATE_PAUSED
    if all(s == THREAD_DONE for s in statuses):
        return STATE_DONE
    # residual:threads 存在,无 running/failed/done(如全 pending / None)→ task in-flight
    return STATE_RUNNING
