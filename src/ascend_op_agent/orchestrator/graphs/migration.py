"""Path-B 迁移图(B1 CUDA / B2 Triton)。

骨架:frontend_parse(CUDA or Triton)→ design → codegen → review_fix →
compile → precision → done。

U10 实现:

- ``build_migration_graph(source_type="cuda", ...)``:CUDA frontend_parse 节点
  (scoped cuda2ascend-simt),产出 OpInfo(migration_strategy=cuda_to_ascendc)+
  ArchitectureMapping(基于 skill api-mapping)
- 后续节点与 ``new_dev`` 共享设计(design/codegen/review_fix/compile/precision)
- design 节点是 HITL(用户批准迁移方案)

U11 实现:

- ``build_migration_graph(source_type="triton", ...)``:triton_frontend 节点
  (scoped triton 5-skill 链入口:task-extractor / op-designer / op-coding /
  op-verifier / latency-optimizer),产出 OpInfo(migration_strategy=triton_to_ascendc)
- 后续节点 design/codegen/review_fix 与 cuda 同形,skill 由 frontend_skill_text
  按 source_type 解析(cannbot_loader SKILL_BUNDLES 已含 triton_frontend 5 路径)
"""

from __future__ import annotations

from typing import Callable, Optional

from ascend_op_agent.orchestrator.cannbot_loader import (
    build_skill_bundle,
    render_skill_bundle_text,
)
from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.nodes.delivery import (
    make_delivery_mode_node,
    make_framework_adapt_node,
)
from ascend_op_agent.orchestrator.nodes.hitl import make_hitl_llm_node
from ascend_op_agent.orchestrator.nodes.migration import (
    make_cuda_frontend_node,
    make_triton_frontend_node,
)
from ascend_op_agent.orchestrator.state_machine import Node, PhaseCallback, PhaseRunner


_SUPPORTED_SOURCE_TYPES = {"cuda", "triton"}


def _resolve_frontend_skill_text(
    source_type: str,
    use_real_skill_bundles: bool,
    explicit: Optional[str],
) -> Optional[str]:
    """构造前端节点的 cannbot skill Layer 6 文本。"""
    if explicit is not None:
        return explicit
    if not use_real_skill_bundles:
        return None

    if source_type == "cuda":
        skills = build_skill_bundle(phase="cuda_frontend", graph="migration")
    elif source_type == "triton":
        skills = build_skill_bundle(phase="triton_frontend", graph="migration")
    else:
        return None

    return render_skill_bundle_text(skills, phase=f"{source_type}_frontend") or None


def build_migration_graph(
    store: CheckpointStore,
    source_type: str,
    agent_factory: Optional[AgentFactory] = None,
    phase_callback: Optional[PhaseCallback] = None,
    frontend_skill_text: Optional[str] = None,
    use_real_skill_bundles: bool = False,
    compile_node_factory: Optional[Callable[[], Node]] = None,
    precision_node_factory: Optional[Callable[[], Node]] = None,
    compile_fix_loop_node_factory: Optional[Callable[[], Node]] = None,
    precision_fix_loop_node_factory: Optional[Callable[[], Node]] = None,
) -> PhaseRunner:
    """构造 Path-B 迁移图。

    Args:
        store: CheckpointStore 实例
        source_type: ``"cuda"`` 或 ``"triton"``(U10 实现 cuda,U11 实现 triton)
        agent_factory: 返回 fresh AIAgent 的工厂
        phase_callback: PhaseRunner 阶段事件回调
        frontend_skill_text: 显式覆盖 frontend 节点的 cannbot skill 文本
        use_real_skill_bundles: True 时从 cannbot submodule 加载真实 skill
        compile_node_factory: 自定义 compile 节点工厂(U13 注入真 compile_node)
        precision_node_factory: 自定义 precision 节点工厂(U13 注入)

    Returns:
        PhaseRunner —— invoke/resume 入口

    Raises:
        ValueError: ``source_type`` 不在 ``{"cuda", "triton"}`` 中
    """
    if source_type not in _SUPPORTED_SOURCE_TYPES:
        raise ValueError(
            f"Unsupported source_type: {source_type}. " f"Supported: {_SUPPORTED_SOURCE_TYPES}"
        )
    if agent_factory is None:
        raise ValueError(
            "agent_factory required (production wires AIAgent with session_manager=None)"
        )

    # ---- 前端节点(cuda / triton 二选一) ----
    frontend_skill = _resolve_frontend_skill_text(
        source_type=source_type,
        use_real_skill_bundles=use_real_skill_bundles,
        explicit=frontend_skill_text,
    )

    if source_type == "cuda":
        frontend_node = make_cuda_frontend_node(
            agent_factory=agent_factory,
            skill_bundle_text=frontend_skill,
            phase_name="cuda_frontend",
        )
        frontend_phase_name = "cuda_frontend"
    else:  # source_type == "triton"
        frontend_node = make_triton_frontend_node(
            agent_factory=agent_factory,
            skill_bundle_text=frontend_skill,
            phase_name="triton_frontend",
        )
        frontend_phase_name = "triton_frontend"

    # ---- design 节点(HITL,共享 new_dev 设计) ----
    def _migration_design_payload_builder(state: dict, update: dict) -> dict:
        return {
            "phase": "design",
            "message": f"请确认{source_type} → Ascend C 迁移方案以继续 codegen 阶段",
            "op_info": state.get("op_info"),
            "arch_mapping": (state.get("design_doc") or {}).get("arch_mapping"),
            "options": ["approve", "reject"],
        }

    design_node = make_hitl_llm_node(
        phase="design",
        task_prompt_template=(
            f"你是 {source_type.upper()} → Ascend C 迁移架构师。基于前端解析的 OpInfo 和\n"
            "ArchitectureMapping,产出迁移 DESIGN.md:\n\n"
            "状态(OpInfo + arch_mapping + 历史):\n{state}\n\n"
            "包含:Tiling 策略、API 映射(从 scoped skill 查)、数据流、\n"
            "downgrade/blocked/excluded 分类、文件清单、测试计划。"
        ),
        interrupt_payload_builder=_migration_design_payload_builder,
        skill_bundle_text=frontend_skill,
        agent_factory=agent_factory,
    )

    # ---- codegen / review_fix(共享 new_dev 设计) ----
    codegen_node = make_llm_node(
        phase="codegen",
        task_prompt_template=(
            f"你是 Ascend C developer。基于已确认的 {source_type.upper()} → Ascend C "
            "迁移 DESIGN.md 生成 AscendC kernel:\n\n"
            "{state}\n\n"
            "输出:kernel.cpp + op.cpp 文件内容(参考 scoped skill 的 grammar 与约束)。"
        ),
        skill_bundle_text=frontend_skill,
        agent_factory=agent_factory,
    )

    review_fix_node = make_llm_node(
        phase="review_fix",
        task_prompt_template=(
            f"你是 Ascend C reviewer。审查 {source_type.upper()} → Ascend C "
            "迁移产出的代码是否保持源行为:\n\n"
            "{state}\n\n"
            "重点:无 silent downgrade、API 映射正确、dtype 覆盖完整。\n"
            "输出:问题列表 + 修复建议;无问题时返回 LGTM。"
        ),
        skill_bundle_text=frontend_skill,
        agent_factory=agent_factory,
    )

    # ---- compile / precision(占位,U13 替换) ----
    # U3: 优先用 fix_loop 包装(review+fix 闭环,跨轮 messages 走 reducer)
    if compile_fix_loop_node_factory is not None:
        compile_node = compile_fix_loop_node_factory()
    elif compile_node_factory is not None:
        compile_node = compile_node_factory()
    else:

        def _placeholder_compile(state: dict) -> dict:
            return {
                "compile_result": {
                    "success": True,
                    "command": "(placeholder)",
                    "stdout": "",
                    "stderr": "",
                    "return_code": 0,
                },
            }

        compile_node = Node(name="compile", func=_placeholder_compile)

    if precision_fix_loop_node_factory is not None:
        precision_node = precision_fix_loop_node_factory()
    elif precision_node_factory is not None:
        precision_node = precision_node_factory()
    else:

        def _placeholder_precision(state: dict) -> dict:
            return {
                "precision_report": {
                    "operator_name": (state.get("op_info") or {}).get("name", "unknown"),
                    "total_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                },
            }

        precision_node = Node(name="precision", func=_placeholder_precision)

    # ---- 交付模式(U12)----
    delivery_mode_node = make_delivery_mode_node(phase="delivery_mode")
    framework_adapt_node = make_framework_adapt_node(
        phase="framework_adapt",
        agent_factory=agent_factory,
        skill_bundle_text=frontend_skill,  # 与前端共用 cuda2ascend-simt / triton skill
    )

    def _done_node(state: dict) -> dict:
        return {"__status__": "done"}

    nodes = [
        Node(name="entry", func=lambda s: {}),
        frontend_node,
        design_node,
        codegen_node,
        review_fix_node,
        compile_node,
        precision_node,
        delivery_mode_node,
        framework_adapt_node,
        Node(name="done", func=_done_node),
    ]

    return PhaseRunner(nodes=nodes, store=store, phase_callback=phase_callback)
