"""HITL 节点工厂(基于 make_llm_node)。

封装"LLM 跑完 → 请求用户确认 → resume 时跳过 LLM 推进"模式。这是
design / delivery_mode 这类需要用户确认的节点的通用模板。

契约(P0-3 修正的 idempotency):

- 第一次调用(``state["pending_confirmation"]`` 为 None):
  1. 跑 LLM(``make_llm_node`` 内部完成)
  2. 从 update 提取关键字段(如 ``state["design_doc"]``)放进 interrupt payload
  3. 返回 update + ``__interrupt__``
- 第二次调用(resume 后 ``state["pending_confirmation"]`` 非空):
  - ``make_llm_node`` 早返回(跳过 LLM)
  - 本 wrapper 仅 return 该 update,推进到下一节点
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.state_machine import Node


PayloadBuilder = Callable[[dict, dict], dict]


def make_hitl_llm_node(
    phase: str,
    task_prompt_template: str,
    interrupt_payload_builder: PayloadBuilder,
    skill_bundle_text: Optional[str] = None,
    agent_factory: Optional[AgentFactory] = None,
    skill_names: Optional[list[str]] = None,
) -> Node:
    """构造 HITL LLM 节点。

    Args:
        phase: 阶段名
        task_prompt_template: 任务 prompt 模板(传给 make_llm_node)
        interrupt_payload_builder: ``callable(state, update) -> dict``,
            从 LLM 输出 + 当前 state 提取要请求确认的内容
            (如 ``{"phase":"design","design_doc":..., "options":["approve","reject"]}``)
        skill_bundle_text: cannbot skill 文本(传给 make_llm_node)
        agent_factory: AIAgent 工厂
        skill_names: cannbot skill 名(传给 make_llm_node 做 U2 跟踪)

    Returns:
        Node —— 第一次产 __interrupt__,resume 时推进
    """
    base_node = make_llm_node(
        phase=phase,
        task_prompt_template=task_prompt_template,
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
        skill_names=skill_names,
    )

    def _hitl_node(state: dict) -> dict:
        update = base_node.func(state)

        # resume 路径(pending 已设):base_node 已跳过 LLM,直接推进
        if state.get("pending_confirmation") is not None:
            return update

        # 首次路径:LLM 跑完,产 interrupt
        interrupt_payload = interrupt_payload_builder(state, update)
        update["__interrupt__"] = interrupt_payload
        return update

    return Node(name=phase, func=_hitl_node)
