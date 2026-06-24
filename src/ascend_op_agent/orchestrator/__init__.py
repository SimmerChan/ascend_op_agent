"""自研轻量状态机编排器。

包裹现有 AIAgent ReAct 核心作为 LLM 节点,消费 cannbot-skills 作知识层,
补 checkpoint/崩溃恢复/阶段化/统一验证/NPU 执行。不引入 LangGraph。

详见 docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md。
"""

from ascend_op_agent.orchestrator.cannbot_loader import (
    CANNBOT_ROOT,
    CannbotSkill,
    build_skill_bundle,
    load_skill,
)

__all__ = [
    "CANNBOT_ROOT",
    "CannbotSkill",
    "build_skill_bundle",
    "load_skill",
]
