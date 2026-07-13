"""自研轻量状态机编排器。

包裹现有 AIAgent ReAct 核心作为 LLM 节点,消费 cannbot-skills 作知识层,
补 checkpoint/崩溃恢复/阶段化/统一验证/NPU 执行。不引入 LangGraph。

详见 docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md。
"""

from ascend_op_agent.orchestrator.cannbot_loader import (
    CANNBOT_ROOT,
    CANBOT_BUNDLE_MAP,
    CannbotSkill,
    SKILL_BUNDLES,
    build_skill_bundle,
    list_cannbot_skill_names,
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
from ascend_op_agent.orchestrator.graphs.migration import build_migration_graph
from ascend_op_agent.orchestrator.graphs.new_dev import build_new_dev_graph
from ascend_op_agent.orchestrator.nodes.common import make_llm_node
from ascend_op_agent.orchestrator.nodes.hitl import make_hitl_llm_node
from ascend_op_agent.orchestrator.nodes.delivery import (
    make_delivery_mode_node,
    make_framework_adapt_node,
    recommend_delivery_mode,
)
from ascend_op_agent.orchestrator.nodes.migration import (
    extract_structured_output,
    make_cuda_frontend_node,
    make_triton_frontend_node,
)
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
from ascend_op_agent.orchestrator.fix_loop import (
    ReviewResult,
    compress_transcript,
    make_fix_loop_node,
    run_fix_loop,
)
from ascend_op_agent.orchestrator.npu_exec import (
    CompileOutcome,
    NpuExecutor,
    PrecisionMetrics,
)
from ascend_op_agent.orchestrator.nodes.validation import (
    make_compile_fix_loop_node,
    make_compile_fix_node,
    make_precision_fix_loop_node,
    make_precision_fix_node,
    make_real_compile_node,
    make_real_precision_node,
    operator_path_from_code_result,
    test_cases_from_state,
)

__all__ = [
    # cannbot
    "CANNBOT_ROOT",
    "CANBOT_BUNDLE_MAP",
    "CannbotSkill",
    "SKILL_BUNDLES",
    "build_skill_bundle",
    "list_cannbot_skill_names",
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
    "make_cuda_frontend_node",
    "make_triton_frontend_node",
    "make_delivery_mode_node",
    "make_framework_adapt_node",
    "recommend_delivery_mode",
    # migration helpers
    "extract_structured_output",
    # fix loop
    "ReviewResult",
    "run_fix_loop",
    "make_fix_loop_node",
    "compress_transcript",
    # NPU exec (U13)
    "NpuExecutor",
    "CompileOutcome",
    "PrecisionMetrics",
    "make_real_compile_node",
    "make_real_precision_node",
    "make_compile_fix_node",
    "make_precision_fix_node",
    "make_compile_fix_loop_node",
    "make_precision_fix_loop_node",
    "operator_path_from_code_result",
    "test_cases_from_state",
    # graphs
    "build_new_dev_graph",
    "build_migration_graph",
]
