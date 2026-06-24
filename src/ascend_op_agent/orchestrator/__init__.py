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
    render_skill_bundle_text,
)
from ascend_op_agent.orchestrator.checkpoint import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_WAITING_CONFIRM,
    Artifact,
    CheckpointStore,
    PendingApproval,
    PendingCheckpoint,
)
from ascend_op_agent.orchestrator.graphs.new_dev import build_new_dev_graph
from ascend_op_agent.orchestrator.nodes.common import make_llm_node
from ascend_op_agent.orchestrator.nodes.hitl import make_hitl_llm_node
from ascend_op_agent.orchestrator.state import (
    APPEND_FIELDS,
    MERGE_FIELDS,
    OpState,
    initial_state,
)
from ascend_op_agent.orchestrator.state_machine import (
    Node,
    PhaseCallback,
    PhaseRunner,
    entry_node_factory,
)

__all__ = [
    # cannbot
    "CANNBOT_ROOT",
    "CannbotSkill",
    "build_skill_bundle",
    "load_skill",
    "render_skill_bundle_text",
    # checkpoint
    "CheckpointStore",
    "PendingCheckpoint",
    "PendingApproval",
    "Artifact",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_WAITING_CONFIRM",
    "STATUS_DONE",
    "STATUS_FAILED",
    # state
    "OpState",
    "APPEND_FIELDS",
    "MERGE_FIELDS",
    "initial_state",
    # state machine
    "Node",
    "PhaseRunner",
    "PhaseCallback",
    "entry_node_factory",
    # node factories
    "make_llm_node",
    "make_hitl_llm_node",
    # graphs
    "build_new_dev_graph",
]
