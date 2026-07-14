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

"""U4 显式命令逻辑(一期-a explicit,不经 LLM 分类器)。

命令:list / new / select / progress / run。CLI(cli.py task 子命令组)与 TUI
chat(/task /progress)共用的纯逻辑层。一期-a 无 LLM 路由分类器(R5 一期-b),
全显式命令。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ascend_op_agent.task_store import TASK_TYPES, TaskStore
from ascend_op_agent.task_store.progress import get_task_progress, list_progress


class NoActiveTaskError(Exception):
    """无 active task 时 progress/run 需显式 task_id。"""


class TaskCommands:
    """显式命令逻辑(一期-a)。CLI 与 chat 共用。

    Args:
        store: TaskStore。
        checkpoint_store: CheckpointStore(progress 聚合用);None 时 list/progress 不可用。
        router: TaskRouter(run dispatch 用);None 时 run 不可用。
    """

    def __init__(
        self,
        store: TaskStore,
        checkpoint_store: Optional[Any] = None,
        router: Optional[Any] = None,
    ):
        self.store = store
        self.ck = checkpoint_store
        self.router = router

    def new(self, task_type: str, object_payload: Optional[dict] = None) -> str:
        """建 task + 设为 active(R7 一期-a explicit)。"""
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type}; expected one of {TASK_TYPES}")
        tid = self.store.create_task(task_type, object_payload)
        self.store.set_active(tid)
        return tid

    def select(self, task_id: str) -> str:
        """显式选/切 active task(R5 一期-a explicit)。

        R16 dogfood 埋点:若从另一个已有 active task 切到本 task(prev 非空且不同),
        记一条 spontaneous_task_switch 事件。首次选(prev=None)/重选当前(prev==to)不计
        —— 忠于"多任务并行切换"语义(DOGFOOD_GUIDE 场景 A);new() 不经此路径故不计。
        """
        if self.store.get_task(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
        prev = self.store.get_active()
        self.store.set_active(task_id)
        if prev is not None and prev != task_id:
            self.store.record_metric("spontaneous_task_switch", from_task=prev, to_task=task_id)
        return task_id

    def flag_complaint(self, detail: str = "") -> str:
        """一键标记 context-juggling 抱怨(R16 dogfood 主观 metric)。

        用户感到"上下文混乱/进展不可查"时手动触发(CLI `task complain`)。
        纯主观,无客观代理;记一条 context_juggling_complaint 事件供 falsifier 汇总。
        """
        return self.store.record_metric(
            "context_juggling_complaint", detail={"note": detail} if detail else None
        )

    def list(self) -> List[Dict[str, Any]]:
        """列任务 + state(rollup)+ thread 数(R8)。需 checkpoint_store。"""
        if self.ck is None:
            raise RuntimeError("checkpoint_store required for list (progress aggregation)")
        return list_progress(self.store, self.ck)

    def progress(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """单任务进展(R8)。task_id 缺省取 active;无 active 抛 NoActiveTaskError。"""
        if self.ck is None:
            raise RuntimeError("checkpoint_store required for progress")
        tid = task_id or self.store.get_active()
        if tid is None:
            raise NoActiveTaskError("no active task; select one or specify task_id")
        return get_task_progress(tid, self.store, self.ck)

    def run(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """dispatch task(一期-a:develop → PhaseRunner)。需 router。"""
        if self.router is None:
            raise RuntimeError("router required for run (executor dispatch)")
        return self.router.dispatch(task_id, user_input, thread_id)
