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

"""任务管理层 数据模型(R1/R15)。

task = (type, object_payload, state)。type 决定执行器(R11-14);state 由 R15
rollup 推导(running/done/failed 自 thread states)+ task-layer-native(draft/paused,
CheckpointStore 无这俩,KTD3)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

# 任务类型(R1)—— object(model/op)是 payload 非 type 维度
TASK_TYPE_MIGRATE = "migrate"
TASK_TYPE_ANALYZE = "analyze"
TASK_TYPE_OPTIMIZE = "optimize"
TASK_TYPE_DEVELOP = "develop"
TASK_TYPES = (TASK_TYPE_MIGRATE, TASK_TYPE_ANALYZE, TASK_TYPE_OPTIMIZE, TASK_TYPE_DEVELOP)

# 任务对外状态(R15)
STATE_DRAFT = "draft"  # task-layer-native:建 task 后未 spawn thread
STATE_RUNNING = "running"  # rollup: 任一 thread running/waiting_confirm
STATE_PAUSED = "paused"  # task-layer-native:用户显式暂停(无 thread 层 analog)
STATE_DONE = "done"  # rollup: 全 thread done
STATE_FAILED = "failed"  # rollup: 任一 thread failed 且无 running

# state_native 标志(存 tasks.state_native 列;draft 由"无 thread"推导,不存)
STATE_NATIVE_NONE = ""
STATE_NATIVE_PAUSED = "paused"

# thread statuses(镜像 CheckpointStore,R15 rollup 输入)
THREAD_PENDING = "pending"
THREAD_RUNNING = "running"
THREAD_WAITING_CONFIRM = "waiting_confirm"
THREAD_DONE = "done"
THREAD_FAILED = "failed"


@dataclass
class Task:
    """任务记录(R1)。

    ``state`` 不持久化(由 rollup 实时推导,best-effort eventually-consistent,
    永不作为 source of truth);``state_native`` 存 task-layer-native 标志(paused)。
    """

    id: str
    type: str
    object_payload: Dict[str, Any] = field(default_factory=dict)
    state_native: str = STATE_NATIVE_NONE
    created_at: str = ""
    updated_at: str = ""
