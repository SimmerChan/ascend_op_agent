"""PhaseRunner — 自研轻量状态机编排器(无 LangGraph)。

顺序 DAG 执行器。每个节点是 ``Callable[[OpState], dict]``,返回 update dict。
PhaseRunner 负责:

- 按 ``nodes`` 列表顺序执行
- 应用 reducer(APPEND_FIELDS / MERGE_FIELDS / last-write-wins)
- 每节点后 ``CheckpointStore.save`` 持久化状态
- 处理 ``__interrupt__``(HITL 暂停)和 ``__status__``(done/failed 终止)
- ``resume`` 从 checkpoint 续跑(崩溃恢复 + HITL 恢复)

为什么不直接用 LangGraph:P0 单算子顺序执行,自研 50 行的 PhaseRunner
够用且更易调试。LangGraph 的复杂路由/分支条件留到 P1(选择性批量迁移)
再评估引入。

节点协议
-------

节点返回的 update dict 可包含两类键:

**普通字段** —— 走 reducer:

- ``messages`` / ``phase_history``: append(累加)
- ``memory_pools`` / ``retry_counts``: dict merge(累加)
- 其余字段: last-write-wins

**控制字段**(下划线前缀,不进 OpState):

- ``__interrupt__: dict`` —— 触发 HITL 暂停,payload 存 pending_approvals,
  status=waiting_confirm。``resume`` 时 payload 注入 ``state["pending_confirmation"]``
  并重跑当前节点(节点需感知 pending_confirmation 做幂等)
- ``__status__: "done" | "failed"`` —— 终止执行
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ascend_op_agent.orchestrator.checkpoint import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_CONFIRM,
    CheckpointStore,
)
from ascend_op_agent.orchestrator.state import (
    APPEND_FIELDS,
    MERGE_FIELDS,
    OpState,
    initial_state,
)

logger = logging.getLogger(__name__)


NodeFunc = Callable[[OpState], dict]
PhaseCallback = Callable[[str, str], None]


@dataclass
class Node:
    """节点定义:name + func。

    Args:
        name: 节点唯一标识(用作 current_phase)
        func: ``Callable[[OpState], dict]``,返回 update dict
    """

    name: str
    func: NodeFunc


def apply_update(state: dict, update: dict) -> None:
    """应用 update dict 到 state(走 reducer)—— module-level,供 fix_loop 复用。

    reducer 规则(与 PhaseRunner._apply_update 一致):
    - APPEND_FIELDS(messages / phase_history):list extend(累积,不覆盖)
    - MERGE_FIELDS(memory_pools / retry_counts):dict update
    - 其他:last-write-wins 覆盖
    - 控制字段(``__interrupt__`` / ``__status__``)不进 state(调用方处理)

    U3 修复:之前 fix_loop 手写 merge 对 list 字段(如 messages)做 ``state[key]=value``
    覆盖,丢失跨轮 conversation 历史(见 fix_loop.py 旧 136-140)。抽到 module-level
    后 fix_loop 复用本函数,messages 正确 extend。
    """
    for key, value in update.items():
        if key in ("__interrupt__", "__status__"):
            continue
        if key in APPEND_FIELDS:
            state.setdefault(key, []).extend(value)
        elif key in MERGE_FIELDS:
            state.setdefault(key, {}).update(value)
        else:
            state[key] = value


class PhaseRunner:
    """顺序 DAG 状态机。

    用法::

        runner = PhaseRunner(
            nodes=[
                Node("entry", entry_node),
                Node("design", make_llm_node(...)),
                Node("done", done_node),
            ],
            store=CheckpointStore(...),
        )
        state = runner.invoke("design trivial add op", thread_id="t1")
        # 中断时:
        state = runner.resume("t1", payload={"approved": True})
    """

    def __init__(
        self,
        nodes: list[Node],
        store: CheckpointStore,
        phase_callback: Optional[PhaseCallback] = None,
    ):
        if not nodes:
            raise ValueError("PhaseRunner requires at least one node")
        self.nodes = nodes
        self._node_index: dict[str, int] = {
            n.name: i for i, n in enumerate(nodes)
        }
        self.store = store
        self._phase_callback = phase_callback

    # ---- 公共 API ----

    def invoke(
        self,
        user_input: str,
        thread_id: str,
        task_type: Optional[str] = None,
    ) -> OpState:
        """启动新 thread。

        Args:
            user_input: 用户输入。
            thread_id: thread 标识。
            task_type: 可选,TaskRouter 传入的任务类型(如 ``"develop"`` /
                ``"migrate"`` / ``"analyze"`` / ``"optimize"``)。None 时不写
                ``state["task_type"]``(下游 ``state.get("task_type")`` 返回 None)。
                **不在 invoke 前 dict-assign state**:state 由 ``initial_state``
                内部创建,调用方无法在 invoke 前设置(与 A2 `_pending_input` 同类
                坑避免)。U1 skill-crystallization 需要 task_type 传透到
                AIAgent → PromptBuilder(Layer 6 降级前置)。
        """
        state = initial_state(thread_id)
        state["messages"] = [{"role": "user", "content": user_input}]
        if task_type is not None:
            state["task_type"] = task_type
        self.store.save(
            thread_id, state, current_phase="", status=STATUS_RUNNING
        )
        logger.info(
            f"PhaseRunner.invoke thread={thread_id} task_type={task_type}"
        )
        return self._run_from(state, start_index=0)

    def resume(
        self,
        thread_id: str,
        payload: Optional[dict] = None,
    ) -> OpState:
        """从 checkpoint 续跑。

        两种场景(由 ``consume_pending`` 结果区分):

        - **HITL 恢复**:pending_approvals 有记录 → payload 注入
          ``pending_confirmation`` → 从 current_phase 重跑同一节点
          (节点应感知 pending_confirmation 做幂等)
        - **崩溃恢复**:无 pending → 从 current_phase 的下一个节点续跑
          (current_phase 已完成,产物落盘)

        Args:
            thread_id: thread 标识
            payload: 可选,显式提供确认 payload(优先级低于 pending_approvals)
        """
        state = self.store.load(thread_id)
        if state is None:
            raise ValueError(f"No checkpoint found for thread {thread_id}")

        pending = self.store.consume_pending(thread_id)
        if pending is not None:
            # stored pending 是 interrupt 时的 default payload(含 phase/options/...)
            # explicit payload 是用户 resume 时的决策 —— 二者 merge,explicit 优先
            base = pending.payload or {}
            if payload is not None:
                merged = {**base, **payload}
                state["pending_confirmation"] = merged
            else:
                state["pending_confirmation"] = base
            logger.info(
                f"PhaseRunner.resume HITL thread={thread_id} phase={pending.phase}"
            )
        elif payload is not None:
            state["pending_confirmation"] = payload
            logger.info(f"PhaseRunner.resume explicit-payload thread={thread_id}")

        current_phase = state.get("current_phase") or ""
        if current_phase not in self._node_index:
            start_index = 0
        else:
            idx = self._node_index[current_phase]
            status = self.store.get_status(thread_id)
            # HITL 恢复(pending 有):重跑当前节点
            # 崩溃 mid-node(status=failed):重跑当前节点
            # 崩溃 between-node(status=running):下一节点(当前节点已落盘成功)
            if pending is not None or status == STATUS_FAILED:
                start_index = idx
            else:
                start_index = idx + 1

        return self._run_from(state, start_index=start_index)

    # ---- 内部 ----

    def _emit_phase(
        self, phase: str, event: str, error: Optional[str] = None
    ) -> None:
        # 统一传 dict(失败时打包 {"error": str}),避免回调收到 str 期望 dict
        payload: dict = {} if error is None else {"error": error}
        if self._phase_callback is not None:
            try:
                self._phase_callback(phase, event, payload)
            except Exception as e:
                logger.warning(f"phase_callback error: {e}")

    def _apply_update(self, state: OpState, update: dict) -> None:
        """应用 update dict 到 state(走 reducer,委托 module-level apply_update)。

        控制字段(``__interrupt__`` / ``__status__``)由调用方处理,不进 state。
        """
        apply_update(state, update)

    def _run_from(self, state: OpState, start_index: int) -> OpState:
        thread_id = state["thread_id"]

        for i in range(start_index, len(self.nodes)):
            node = self.nodes[i]
            self._emit_phase(node.name, "started")
            state["current_phase"] = node.name

            try:
                update = node.func(state)
            except Exception as e:
                logger.exception(f"Node {node.name} crashed")
                self._emit_phase(node.name, "failed", error=str(e))
                self.store.save(
                    thread_id,
                    state,
                    current_phase=node.name,
                    status=STATUS_FAILED,
                )
                raise

            self._apply_update(state, update)

            # HITL 中断
            if "__interrupt__" in update:
                payload = update["__interrupt__"]
                state["pending_confirmation"] = payload
                # 先 save state(含 current_phase=node.name + pending_confirmation),
                # 再 mark_waiting 改 status —— 否则 store.load 返回的 state 会
                # 滞留在上一节点,resume 误从中断点之前的节点重跑
                self.store.save(
                    thread_id,
                    state,
                    current_phase=node.name,
                    status=STATUS_RUNNING,
                )
                self.store.mark_waiting(thread_id, node.name, payload)
                self._emit_phase(node.name, "interrupted")
                logger.info(
                    f"Node {node.name} interrupted (HITL) thread={thread_id}"
                )
                return state

            # 显式终止
            terminal_status = update.get("__status__")
            if terminal_status in (STATUS_DONE, STATUS_FAILED):
                state.setdefault("phase_history", []).append(node.name)
                self.store.save(
                    thread_id,
                    state,
                    current_phase=node.name,
                    status=terminal_status,
                )
                self._emit_phase(
                    node.name,
                    "completed" if terminal_status == STATUS_DONE else "failed",
                )
                logger.info(
                    f"Node {node.name} terminal={terminal_status} thread={thread_id}"
                )
                return state

            # 正常完成:落 checkpoint + 继续下一节点
            state.setdefault("phase_history", []).append(node.name)
            self.store.save(
                thread_id,
                state,
                current_phase=node.name,
                status=STATUS_RUNNING,
            )
            self._emit_phase(node.name, "completed")

        # 跑完所有节点未显式 done:标记 done
        self.store.save(
            thread_id,
            state,
            current_phase=state.get("current_phase", ""),
            status=STATUS_DONE,
        )
        logger.info(f"PhaseRunner reached end thread={thread_id}")
        return state


def entry_node_factory() -> Node:
    """占位 entry 节点。

    invoke() 已经把 user_input 写入 messages,这里仅推进 current_phase。
    保留此节点是为了让 phase_history 有一条 entry 记录(便于排查)。
    """

    def _entry(state: OpState) -> dict:
        return {"phase_history": []}  # 占位 update,reducer 不影响

    return Node(name="entry", func=_entry)
