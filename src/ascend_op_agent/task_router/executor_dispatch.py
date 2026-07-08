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

"""U3 executor dispatch —— 按 task.type 路由到执行器。

一期-a 仅 develop(复用 PhaseRunner,R14)。insertion point(F3 已定):task 层只接
non-op: 输入(op: 仍直走 PhaseRunner);develop type 时 dispatch 内转 op: 调用
``orchestrator.invoke``。

migrate/analyze/optimize 一期-a gated(U8 接入;migrate delivery-gated on Path A,
analyze/optimize passthrough 到外部 executor)。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from ascend_op_agent.task_store import TASK_TYPE_DEVELOP, TaskStore


class TaskGatedError(Exception):
    """任务类型一期-a 不可启动(gated on Path A / 外部 executor / 一期-b)。"""


class TaskExecutorUnavailable(Exception):
    """执行器未接线(develop 的 PhaseRunner orchestrator=None)。"""


class TaskRouter:
    """按 task.type 路由到执行器。

    Args:
        task_store: TaskStore(任务 + thread 关联)。
        orchestrator: PhaseRunner 实例(develop 执行器);一期-a 由调用方注入,
            None 时 develop 不可用(op: 路由未接线)。
    """

    def __init__(self, task_store: TaskStore, orchestrator: Optional[Any] = None):
        self.store = task_store
        self.orchestrator = orchestrator

    def dispatch(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按 task.type 路由(R14 develop / 一期-a 其余 gated)。

        Returns:
            ``{"thread_id", "state"}``(develop:PhaseRunner.invoke 返回的 state)。
        """
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")

        if task.type == TASK_TYPE_DEVELOP:
            return self._dispatch_develop(task_id, user_input, thread_id)

        # 一期-a stub:migrate/analyze/optimize gated
        raise TaskGatedError(
            f"task type '{task.type}' not startable in 一期-a "
            f"(gated on Path A / 外部 executor;见 U8 + Dependencies)"
        )

    def _dispatch_develop(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str],
    ) -> Dict[str, Any]:
        if self.orchestrator is None:
            raise TaskExecutorUnavailable(
                "PhaseRunner (orchestrator) not wired — op: 路由未接线(见 backend.py)"
            )
        tid = thread_id or uuid.uuid4().hex[:12]
        # link BEFORE invoke:thread 执行期对 progress 可见 + invoke 失败不孤儿
        # (adversarial/correctness/reliability 共指 P1:原 invoke→link 顺序,link 失败则
        # checkpoint 已写但 thread 永远不被 rollup 看到)
        self.store.link_thread(task_id, tid)
        # develop type → 转 op: 调用 PhaseRunner(insertion point 决策,F3)
        state = self.orchestrator.invoke(user_input, thread_id=tid)
        return {"thread_id": tid, "state": state}
