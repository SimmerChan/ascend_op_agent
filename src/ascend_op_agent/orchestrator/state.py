"""OpState — 编排器状态(TypedDict)。

决策 1 落地:OpState 是 PhaseRunner 在节点间传递的可序列化状态。

关键设计:

- dataclass(OpInfo/DesignDoc/CodeGenResult/CompileResult/PrecisionReport/PhaseResult)
  以 **dict** 形式嵌入(U5 serde 支撑),节点内用 ``from_dict`` 重建 —— SqliteSaver
  要求 JSON 可序列化,dataclass 直接存会失败。
- ``messages`` 是 ``list[{"role","content"}]``,与 ``AIAgent._conversation_history`` 同形,
  rehydrate 时零转换(P0-2 修正:不复用 Entry serde)。
- 累加器字段(reducer):``messages``/``phase_history`` 用 append;``memory_pools``/``retry_counts``
  用 dict merge;其余 last-write-wins。reducer 在 PhaseRunner._apply_update 内手动应用
  (TypedDict 本身不支持 reducer 注解,这是自研与 LangGraph 的区别)。
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


class OpState(TypedDict, total=False):
    """编排器状态。total=False 让所有字段可选(便于节点局部更新)。"""

    # 标识
    thread_id: str

    # dataclass 嵌入(以 dict 形式,JSON 可序列化进 checkpoint)
    op_info: Optional[dict]
    design_doc: Optional[dict]
    code_result: Optional[dict]
    compile_result: Optional[dict]
    precision_report: Optional[dict]
    last_phase_result: Optional[dict]

    # 交付模式(sample/torch_npu/pybind,R5 HITL)
    delivery_mode: Optional[str]

    # 累加器字段 —— reducer 在 PhaseRunner._apply_update 内手动应用
    messages: list[dict]              # list[{"role","content"}] —— append
    memory_pools: dict[str, Any]      # 跨节点 Layer 5 持久化 —— dict merge
    phase_history: list[str]          # 已执行节点名 —— append
    retry_counts: dict[str, int]      # 各节点重试计数 —— dict merge

    # 控制字段 —— last-write-wins
    current_phase: Optional[str]
    pending_confirmation: Optional[dict]

    # U1: TaskRouter 传入的任务类型(如 "develop" / "migrate" / "analyze"
    # / "optimize")。PhaseRunner.invoke(task_type=...) 时写入。下游 LLM 节点
    # 从 state.get("task_type") 取出传给 AIAgent.run_conversation → PromptBuilder
    # → Layer 6(降级前置)。None 时不写入 state,默认路径(/learn 聊天)走 Layer 6
    # 默认只渲染 self-built 段(不渲染 cannbot 全量)。
    task_type: Optional[str]


APPEND_FIELDS: frozenset[str] = frozenset({"messages", "phase_history"})
MERGE_FIELDS: frozenset[str] = frozenset({"memory_pools", "retry_counts"})


def initial_state(thread_id: str) -> OpState:
    """构造空 OpState(thread_id + 空累加器)。"""
    return OpState(
        thread_id=thread_id,
        op_info=None,
        design_doc=None,
        code_result=None,
        compile_result=None,
        precision_report=None,
        last_phase_result=None,
        delivery_mode=None,
        messages=[],
        memory_pools={},
        phase_history=[],
        retry_counts={},
        current_phase=None,
        pending_confirmation=None,
        task_type=None,
    )
