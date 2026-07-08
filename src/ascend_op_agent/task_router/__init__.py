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

"""任务管理层 task_router 包(U3+):按 task.type 路由到执行器。

一期-a:develop → PhaseRunner 复用(insertion point:task 层接 non-op: 输入,
develop type 时 dispatch 转 op: 调用);migrate/analyze/optimize stub(gated on
Path A / 外部 executor,U8 接入)。
"""

from ascend_op_agent.task_router.executor_dispatch import (
    TaskExecutorUnavailable,
    TaskGatedError,
    TaskRouter,
)

__all__ = ["TaskRouter", "TaskGatedError", "TaskExecutorUnavailable"]
