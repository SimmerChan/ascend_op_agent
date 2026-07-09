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

from typing import Any, Callable, Dict, List, Optional

from ascend_op_agent.task_store import TASK_TYPES, TaskStore
from ascend_op_agent.task_store.progress import get_task_progress, list_progress
from ascend_op_agent.task_store.relations import (
    RELATION_TYPES,
    Relation,
    RelationStore,
)
from ascend_op_agent.task_router.relation_builder import (
    RelationBuilder,
    RelationSuggestion,
)


class NoActiveTaskError(Exception):
    """无 active task 时 progress/run 需显式 task_id。"""


class TaskCommands:
    """显式命令逻辑(一期-a)。CLI 与 chat 共用。

    Args:
        store: TaskStore。
        checkpoint_store: CheckpointStore(progress 聚合用);None 时 list/progress 不可用。
        router: TaskRouter(run dispatch 用);None 时 run 不可用。
        relation_store: RelationStore(U5 关系图);None 时从 store.db_path 建(同库)。
        relation_builder: RelationBuilder(suggest 用);None 时 suggest 按需 lazy wiring。
    """

    def __init__(
        self,
        store: TaskStore,
        checkpoint_store: Optional[Any] = None,
        router: Optional[Any] = None,
        relation_store: Optional[RelationStore] = None,
        relation_builder: Optional[RelationBuilder] = None,
    ):
        self.store = store
        self.ck = checkpoint_store
        self.router = router
        # relations:与 TaskStore 同库(KTD9);未注入则从 store.db_path 建
        self.relations = relation_store or RelationStore(store.db_path)
        self._relation_builder = relation_builder

    def new(self, task_type: str, object_payload: Optional[dict] = None) -> str:
        """建 task + 设为 active(R7 一期-a explicit)。"""
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type}; expected one of {TASK_TYPES}")
        tid = self.store.create_task(task_type, object_payload)
        self.store.set_active(tid)
        return tid

    def select(self, task_id: str) -> str:
        """显式选/切 active task(R5 一期-a explicit)。"""
        if self.store.get_task(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
        self.store.set_active(task_id)
        return task_id

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

    # ---- U5: typed graph(suggest + confirm + 手动 add/remove/edit) ----

    def link(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: str,
        confidence: Optional[float] = None,
    ) -> Relation:
        """手动/确认后落库一条关系(R2 add / KTD4 confirm)。validate task 存在。

        Raises:
            KeyError: src/dst task 不存在(commands 层校验;RelationStore FK 不强制)。
            ValueError: relation_type 非法。
            RelationCycleError: 该 type DAG 成环。
        """
        if relation_type not in RELATION_TYPES:
            raise ValueError(
                f"unknown relation type: {relation_type}; expected one of {RELATION_TYPES}"
            )
        self._require_task(src_task_id)
        self._require_task(dst_task_id)
        return self.relations.add_relation(
            src_task_id, dst_task_id, relation_type, confidence=confidence
        )

    def unlink(self, src_task_id: str, dst_task_id: str) -> int:
        """删 src→dst 全部 type 边(R2 remove)。返删除行数(0 = 无边)。"""
        return self.relations.remove_relation(src_task_id, dst_task_id)

    def edit_relation(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: str,
        confidence: Optional[float] = None,
    ) -> Relation:
        """in-place 改 src→dst 边的 type/confidence(R2 edit,不删+重插)。

        Raises:
            KeyError: src/dst task 不存在。
            RelationNotFoundError: src→dst 边不存在。
            RelationCycleError: 改成新 type 后成环。
        """
        self._require_task(src_task_id)
        self._require_task(dst_task_id)
        return self.relations.edit_relation(
            src_task_id, dst_task_id, relation_type, confidence=confidence
        )

    def suggest(
        self,
        new_task_id: Optional[str] = None,
        active_task_id: Optional[str] = None,
        llm_call: Optional[Callable[[str], str]] = None,
    ) -> List[RelationSuggestion]:
        """LLM 推荐关系(KTD4 suggest,**不落库**)。new_task_id 缺省取 active。

        Args:
            llm_call: LLM callable(单测注入 mock);None 且未 wiring builder → 返空。
        """
        tid = new_task_id or self.store.get_active()
        if tid is None:
            raise NoActiveTaskError(
                "no active task; select one or specify new_task_id"
            )
        builder = self._relation_builder or RelationBuilder(
            self.store, llm_call=llm_call
        )
        if llm_call is not None and isinstance(builder, RelationBuilder):
            # 临时注入 callable(单测路径),不覆盖已 wiring 的 builder
            builder.llm_call = llm_call
        return builder.suggest(tid, active_task_id)

    def _require_task(self, task_id: str) -> None:
        if self.store.get_task(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
