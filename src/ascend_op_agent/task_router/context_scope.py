# Copyright 2026 SimperChan
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

"""U6 上下文隔离(KTD5 兜底)+ 受控跨任务读(KTD10 + R10)+ 跨 db 读(KTD11)。

三层职责,都建在 **已 ship 的无副作用 API** 之上:

1. **task-scoped 隔离(KTD5 兜底)**:memory / 对话 / checkpoint 已是 thread_id-keyed。
   active task 执行时,本类把这些 thread-scoped 记录 filter 到 ``thread_id in
   task_threads`` 集合(由 ``TaskStore.get_task_threads(task_id)`` 给出)。**task_id
   维度 memory schema 不引入**(origin 决定;实测 thread-scoped 不足再升级)。

2. **跨 db 读(KTD11)**:复用已 ship 的无参 ``CheckpointStore.list_all_threads()``
   拿全部 thread 记录,任务侧按 ``get_task_threads(task_id)`` 过滤。**绝对不改
   CheckpointStore**(KTD1 kill-switch 隔离),也**不引入带参签名**(feasibility P1
   确认那会冲突 shipped 签名 + 破坏 KTD1)。本类就是 rollup.py ``thread_status_fn``
   跨 db 读模式的同构翻版。

3. **受控跨任务读(KTD10 + R10)**:不复制 src context 到 dst;而是查
   ``RelationStore.list_relations(reader_task_id)`` —— reader 作为 ``src`` 的
   ``depends-on`` 边指向的 ``dst`` task 是可读的产物源 → 读其标过的 artifact 路径
   (只读 artifacts,不读 memory/对话)。**无 depends-on 边 → 跨任务读拒绝(返回空
   + audit log,不崩)**;``spawned-by`` 边无读权限(随关系本身延后,仅 depends-on
   授权)。
"""

from __future__ import annotations

import logging
from typing import List, Set

from ascend_op_agent.task_store.artifacts import Artifact, ArtifactStore
from ascend_op_agent.task_store.relations import (
    RELATION_DEPENDS_ON,
    RelationStore,
)
from ascend_op_agent.task_store.store import TaskStore

logger = logging.getLogger(__name__)


class ContextScope:
    """active task 上下文 task-scoped 隔离 + 受控跨任务读。

    Args:
        store: TaskStore(取 task_threads 映射,KTD1 任务-线程绑定)。
        relation_store: RelationStore(U5,只读用 —— 不改其逻辑)。
        artifact_store: ArtifactStore(KTD10 产物注册表)。
    """

    def __init__(
        self,
        store: TaskStore,
        relation_store: RelationStore,
        artifact_store: ArtifactStore,
    ):
        self.store = store
        self.relation_store = relation_store
        self.artifact_store = artifact_store

    # ---- KTD5 task-scoped 隔离 + KTD11 跨 db 读 ----

    def task_thread_ids(self, task_id: str) -> Set[str]:
        """active task 的 thread_id 集合(隔离 filter 的权威边界)。"""
        return set(self.store.get_task_threads(task_id))

    def filter_threads(self, task_id: str, all_threads: list) -> list:
        """任务侧 thread_id filter(KTD11):把 CheckpointStore 已 ship 的无参
        ``list_all_threads()`` 结果,按 ``get_task_threads(task_id)`` 过滤到本 task。

        **不污染 CheckpointStore** —— filter 在任务侧(本类)做,跨 db 读 = 单 pass
        + 任务侧 filter(eventual consistency 容忍窗口 = rollup 周期)。

        Args:
            task_id: active task。
            all_threads: ``CheckpointStore.list_all_threads()`` 的输出
                (list[PendingCheckpoint],shipped 无参签名,只读)。

        Returns:
            ``all_threads`` 中 ``thread_id in task_threads`` 的子集(同 shape)。
        """
        allowed = self.task_thread_ids(task_id)
        return [t for t in all_threads if t.thread_id in allowed]

    def list_task_checkpoint_records(self, task_id: str, checkpoint_store) -> list:
        """跨 db 读 active task 的 checkpoint 记录(KTD11)。

        复用已 ship 的无参 ``checkpoint_store.list_all_threads()``,任务侧过滤。
        ``checkpoint_store`` 只 read-only 调用,**不改其任何方法**(KTD1)。

        Args:
            task_id: active task。
            checkpoint_store: CheckpointStore 实例(只调其无参 ``list_all_threads``)。

        Returns:
            本 task 的 thread checkpoint 记录子集(与 ``list_all_threads`` 同 shape)。
        """
        all_threads = checkpoint_store.list_all_threads()
        return self.filter_threads(task_id, all_threads)

    # ---- KTD10 + R10 受控跨任务读 ----

    def read_related_artifacts(self, reader_task_id: str) -> List[Artifact]:
        """受控跨任务读:reader 读它 depends-on 的 task 标过的产物(KTD10 + R10)。

        权限 = 解析 ``relations.list_relations(reader_task_id)``:
        - reader 作为 ``src`` 的 ``depends-on`` 边 → 其 ``dst`` task 的 artifacts 可读。
        - 无 depends-on 边 → 跨任务读拒绝(返回空 + audit log,不崩)。
        - ``spawned-by`` 边无读权限(仅 depends-on 授权;R10)。

        只读 artifact 路径注册表,**不读 src 的 memory / 对话**(KTD5 隔离域)。

        Args:
            reader_task_id: 发起跨任务读的 task(作为 depends-on 边的 src)。

        Returns:
            可读 task 的全部 artifact 路径;无 depends-on 边 / 无产物 → 空(不崩)。
        """
        rels = self.relation_store.list_relations(reader_task_id)
        # reader 依赖谁:reader 作 src 的 depends-on 边 → dst 是产物源
        readable_tasks = {
            r.dst_task_id
            for r in rels
            if r.relation_type == RELATION_DEPENDS_ON
            and r.src_task_id == reader_task_id
        }
        if not readable_tasks:
            logger.info(
                "cross-task read rejected for %s: no depends-on edge "
                "(only depends-on grants read; spawned-by/related-to do not)",
                reader_task_id,
            )
            return []
        out: List[Artifact] = []
        for src_task_id in sorted(readable_tasks):
            out.extend(self.artifact_store.get_artifacts(src_task_id))
        return out

    def read_related_artifacts_by_type(
        self, reader_task_id: str, artifact_type: str
    ) -> List[Artifact]:
        """同 read_related_artifacts,但只取指定类型(如只读 report)。"""
        rels = self.relation_store.list_relations(reader_task_id)
        readable_tasks = {
            r.dst_task_id
            for r in rels
            if r.relation_type == RELATION_DEPENDS_ON
            and r.src_task_id == reader_task_id
        }
        if not readable_tasks:
            logger.info(
                "cross-task read (by_type=%s) rejected for %s: no depends-on edge",
                artifact_type,
                reader_task_id,
            )
            return []
        out: List[Artifact] = []
        for src_task_id in sorted(readable_tasks):
            out.extend(
                self.artifact_store.get_artifacts_by_type(src_task_id, artifact_type)
            )
        return out
