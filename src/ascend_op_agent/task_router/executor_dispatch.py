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

"""U3 + U8 executor dispatch —— 按 task.type 路由到执行器(单 dispatch dict)。

一期-a(U3):develop → PhaseRunner 复用(R14)。insertion point(F3 已定):task 层
只接 non-op: 输入;develop type 时 dispatch 内转 op: 调用 ``orchestrator.invoke``。

一期-b(U8):扩到 4 type,用 **单 dispatch dict** 映射 type→bound method(不引入
Executor ABC / executors/ 子目录 —— scope guardian #5:4 文件 ABC 框架对只有 2 stub
的 passthrough 过度膨胀):

- ``develop`` → ``_dispatch_develop``(PhaseRunner.invoke,已 ship;U8 加 writer hook)
- ``migrate`` → ``_dispatch_migrate``(Path A delivery gate,KTD8;gate 过但 Path A
  executor 未接,follow-up plan)
- ``analyze`` → ``_dispatch_analyze``(passthrough,外部 executor 契约 TBD)
- ``optimize`` → ``_dispatch_optimize``(passthrough,外部 executor 契约 TBD)

writer hook(#1 关键约束)**在 dispatch boundary 调用**(不在 PhaseRunner 内部)——
feasibility 指出 orchestrator 内部调用会依赖 tasks.db,破坏 KTD1 kill-switch 隔离。
``_dispatch_develop`` 同时持有 task_id 和 invoke 返回值,在这里提取 artifact 路径并经
``make_writer``(U6 真 ArtifactStore + 真 writer)注册到 task_artifacts_index。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from ascend_op_agent.task_store import TASK_TYPE_DEVELOP, TaskStore
from ascend_op_agent.task_store.artifacts import ArtifactStore, make_writer
from ascend_op_agent.task_store.models import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
)

logger = logging.getLogger(__name__)

#: Path A executor spike 通过标志(KTD8 delivery gate 探测点)。文件存在 = Path A
#: plan-done + executor spike 通过,migrate task 可启动。默认指向用户配置目录;测试
#: 经构造参 ``path_a_spike_path`` 注入 tmp_path 避免污染真 home。
DEFAULT_PATH_A_SPIKE_PATH = Path("~/.ascend_op_agent/path_a.spike_passed").expanduser()

#: written_by 标记(dispatch boundary writer hook 用)。
_WRITTEN_BY_PHASE_RUNNER = "phase_runner"


class TaskGatedError(Exception):
    """任务类型当前不可启动(gated on Path A spike / 一期-b 边界)。

    migrate task 在 Path A delivery gate 未通过时 raise;task state 不变。
    """


class TaskExecutorUnavailable(Exception):
    """执行器未接线(develop 的 PhaseRunner orchestrator=None)。"""


class ExecutorNotImplemented(Exception):
    """外部 executor 契约 TBD / Path A executor 未接(passthrough 边界)。

    analyze / optimize 外部 executor 契约待 follow-up plan 实现;migrate 在 Path A
    gate 通过后但真 Path A executor 未接时同样 raise。raise 它确保不 silent fail
    (plan U8:noop adapter 显式 raise)。
    """


class TaskRouter:
    """按 task.type 路由到执行器(单 dispatch dict,无 ABC)。

    Args:
        task_store: TaskStore(任务 + thread 关联)。
        orchestrator: PhaseRunner 实例(develop 执行器);None 时 develop 不可用。
        artifact_store: 可选 ArtifactStore(KTD10);提供时 develop dispatch boundary
            经 ``make_writer`` 把 PhaseRunner 产出的 artifact 路径注册到
            task_artifacts_index(#1 关键约束:boundary 调用,不在 PhaseRunner 内)。
            None 时不写 artifact(None-safe,develop 路径不 regress)。
        path_a_spike_path: Path A spike 标志文件路径(KTD8 gate 探测点);默认
            ``~/.ascend_op_agent/path_a.spike_passed``。测试注入 tmp_path 避免污染 home。
    """

    def __init__(
        self,
        task_store: TaskStore,
        orchestrator: Optional[Any] = None,
        artifact_store: Optional[ArtifactStore] = None,
        path_a_spike_path: Optional[Union[str, Path]] = None,
    ):
        self.store = task_store
        self.orchestrator = orchestrator
        self.artifact_store = artifact_store
        self.path_a_spike_path = (
            Path(path_a_spike_path)
            if path_a_spike_path is not None
            else DEFAULT_PATH_A_SPIKE_PATH
        )
        # U6 真 writer(lazy:仅 artifact_store 提供时构造;written_by 标 phase_runner)
        self._artifact_writer: Optional[Callable[[str, str, str], None]] = (
            make_writer(artifact_store, written_by=_WRITTEN_BY_PHASE_RUNNER)
            if artifact_store is not None
            else None
        )

    # ---- 公共 dispatch ----

    def dispatch(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按 task.type 路由(单 dispatch dict)。

        Returns:
            develop:``{"thread_id", "state"}``(PhaseRunner.invoke 返回的 state)。
            migrate/analyze/optimize:本 unit 不实现真执行(raise TaskGatedError /
            ExecutorNotImplemented),无正常返回。
        """
        task = self.store.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task: {task_id}")

        # 单 dispatch dict:type → bound method(不引入 Executor ABC,#5 约束)
        handlers: Dict[str, Callable[..., Dict[str, Any]]] = {
            TASK_TYPE_DEVELOP: self._dispatch_develop,
            TASK_TYPE_MIGRATE: self._dispatch_migrate,
            TASK_TYPE_ANALYZE: self._dispatch_analyze,
            TASK_TYPE_OPTIMIZE: self._dispatch_optimize,
        }
        handler = handlers.get(task.type)
        if handler is None:  # 理论不可达(models.TASK_TYPES 已枚举),防御
            raise TaskGatedError(
                f"task type '{task.type}' not routable (unknown type;见 models.TASK_TYPES)"
            )
        return handler(task_id, user_input, thread_id)

    # ---- develop:PhaseRunner(U3 已 ship)+ U8 writer hook ----

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

        # U8 writer hook(#1 关键约束:dispatch boundary 调用,不在 PhaseRunner 内)。
        # 从 invoke 返回的 state 提取 artifact 路径,经 U6 真 make_writer 注册到
        # task_artifacts_index。PhaseRunner 当前不产出 artifact 路径 → None-safe 不写,
        # develop 路径不 regress。节点产产物时在 update dict 设 ``artifacts`` key 即可
        # 自动注册(见 _extract_artifacts_from_state 契约)。
        self._register_artifacts_at_boundary(task_id, state)

        return {"thread_id": tid, "state": state}

    def _register_artifacts_at_boundary(self, task_id: str, state: Any) -> None:
        """dispatch boundary writer hook:从 invoke 返回 state 提取 artifact 路径并注册。

        #1 关键约束:这里(而非 PhaseRunner 内部)是唯一同时持有 task_id 和 invoke
        返回值的位置;在 PhaseRunner 内调用会让 orchestrator 依赖 tasks.db,破坏 KTD1
        kill-switch 隔离(feasibility P1)。

        提取契约见 ``_extract_artifacts_from_state``。无 writer / 无 artifact → no-op
        (None-safe,develop 路径不 regress)。写入失败(store.write 异常)透传,由调用
        方决定;本 unit 不吞错(让 dispatch 失败可见,而非 silent drop artifact 注册)。
        """
        if self._artifact_writer is None:
            return
        for artifact_type, path in _extract_artifacts_from_state(state):
            self._artifact_writer(task_id, artifact_type, path)

    # ---- migrate:Path A delivery gate(KTD8) ----

    def _dispatch_migrate(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str],
    ) -> Dict[str, Any]:
        """migrate → Path A adapter(KTD8 delivery-gated)。

        gate 探测:``path_a_spike_path`` 文件存在 = Path A plan-done + executor spike
        通过。gate 未通过 → ``TaskGatedError``(task state 不变,still draft/paused)。

        gate 通过后的行为(本 unit 选择):raise ``ExecutorNotImplemented`` —— 真 Path A
        executor 未接(follow-up plan)。理由:满足"建标志 → 不抛 gate 错",且不引入
        path_a.py stub 文件(保持 #5 单文件约束);诚实标注 gate 过但执行器未接。
        """
        if not self.path_a_spike_path.exists():
            raise TaskGatedError(
                f"Path A plan-done + executor spike 通过前 migrate task 不可启动 "
                f"(spike 标志缺失: {self.path_a_spike_path})"
            )
        # gate 通过,但 Path A executor 未接(follow-up plan 实现)
        raise ExecutorNotImplemented(
            "Path A delivery gate passed but Path A executor not wired "
            "(follow-up plan);migrate dispatch 不可用"
        )

    # ---- analyze / optimize:外部 executor passthrough(契约 TBD) ----

    def _dispatch_analyze(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str],
    ) -> Dict[str, Any]:
        """analyze → 外部 executor passthrough(契约 TBD,follow-up plan 实现)。

        plan U8 / AE9:passthrough 路由到外部 analyze executor;本 plan 仅 spec 契约,
        显式 raise ``ExecutorNotImplemented`` 确保不 silent fail。
        """
        raise ExecutorNotImplemented(
            "analyze 外部 executor 契约 TBD,follow-up plan 实现"
        )

    def _dispatch_optimize(
        self,
        task_id: str,
        user_input: str,
        thread_id: Optional[str],
    ) -> Dict[str, Any]:
        """optimize → 外部 executor passthrough(契约 TBD,follow-up plan 实现)。

        plan U8 / AE10:passthrough 路由到外部 optimize executor(路径 B / Path A
        native-composition);本 plan 仅 spec 契约,显式 raise ``ExecutorNotImplemented``。
        """
        raise ExecutorNotImplemented(
            "optimize 外部 executor 契约 TBD,follow-up plan 实现"
        )


# ---- artifact 提取(dispatch boundary writer hook 用) ----


def _extract_artifacts_from_state(state: Any) -> List[Tuple[str, str]]:
    """从 PhaseRunner.invoke 返回的 state 提取 ``(artifact_type, path)`` 列表。

    契约:PhaseRunner 节点产产物时在 update dict 设 ``artifacts`` key(reducer 对未知
    key last-write-wins,会进 state)。接受的形态(宽容解析):

    - ``[{"type": "report", "path": "/x/r.md"}, ...]`` —— dict 列表(推荐)
    - ``[("report", "/x/r.md"), ...]`` / ``[["report", "/x/r.md"], ...]`` —— 序列对
    - ``{"report": "/x/r.md"}`` —— 单 type→path 映射

    ``state`` 不是 dict / 无 ``artifacts`` key / 空列表 → 返回 ``[]``(None-safe,
    develop 路径不 regress)。形态不识别的元素跳过(不崩,记 debug log)。

    PhaseRunner 当前(一期-a)的节点不设 ``artifacts``,故真 develop dispatch 走到这里
    返回空、不写任何 artifact —— U8 只是把 boundary writer hook 接到位,等节点开始
    产产物时自动生效。
    """
    if not isinstance(state, dict):
        return []
    raw = state.get("artifacts")
    if not raw:
        return []
    out: List[Tuple[str, str]] = []
    if isinstance(raw, dict):
        # {"report": "/x/r.md", "script": "/x/s.py"}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str):
                out.append((k, v))
            else:
                logger.debug("skip unrecognized artifact entry: %r", (k, v))
        return out
    if not isinstance(raw, list):
        logger.debug("artifacts field not list/dict: %r", type(raw))
        return []
    for item in raw:
        if isinstance(item, dict):
            t = item.get("type")
            p = item.get("path")
            if isinstance(t, str) and isinstance(p, str):
                out.append((t, p))
            else:
                logger.debug("skip artifact dict missing type/path: %r", item)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            t, p = item
            if isinstance(t, str) and isinstance(p, str):
                out.append((t, p))
            else:
                logger.debug("skip artifact pair with non-str members: %r", item)
        else:
            logger.debug("skip unrecognized artifact item: %r", item)
    return out
