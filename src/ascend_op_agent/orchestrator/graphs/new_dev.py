"""Path-C 新开发图(analyze → design → codegen → review_fix → compile → precision)。

P0 MVP 版本(U9):

- 6 个 LLM 节点 + 1 个 done,顺序执行
- design 节点是 HITL(LLM 跑完 → 请求用户批准 → resume 推进)
- compile / compile_fix / precision 在 U9 阶段先用占位节点(返回 success);
  U13 加 NpuExecutor 后替换为真实 cann_compile 调用
- review_fix 在 U9 阶段无条件推进到 compile;U14 加 fix_loop 后补 retry 边

cannbot skill 绑定(决策 3 路径 C 行):

- analyze: ascendc-kernel-architect skill 集(需求分析 + tiling 决策)
- design: ascendc-kernel-architect skill 集(DESIGN.md/PLAN.md)
- codegen: ascendc-kernel-developer skill 集(实现)
- review_fix: ascendc-kernel-reviewer skill 集(review + fix)
- compile: 无 LLM,确定性 cann_compile 调用(U13)
- precision: ascendc-ops-precision-standard skill 集(numpy diff,U13)

本 MVP 阶段 skill_bundle_text 留占位,真 cannbot skill 文本由
``cannbot_loader.build_skill_bundle`` 在 backend.py wiring 时注入。
"""

from __future__ import annotations

from typing import Callable, Optional

from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.nodes.hitl import make_hitl_llm_node
from ascend_op_agent.orchestrator.state_machine import Node, PhaseCallback, PhaseRunner


def _build_phase_2_agent_factory(
    agent_factory: Optional[AgentFactory],
) -> AgentFactory:
    if agent_factory is None:
        raise ValueError(
            "agent_factory required (production wires AIAgent with session_manager=None)"
        )
    return agent_factory


def build_new_dev_graph(
    store: CheckpointStore,
    agent_factory: Optional[AgentFactory] = None,
    phase_callback: Optional[PhaseCallback] = None,
    skill_bundles: Optional[dict[str, str]] = None,
    compile_node_factory: Optional[Callable[[], Node]] = None,
    precision_node_factory: Optional[Callable[[], Node]] = None,
) -> PhaseRunner:
    """构造 Path-C 新开发图。

    Args:
        store: CheckpointStore 实例
        agent_factory: 返回 fresh AIAgent 的工厂
        phase_callback: PhaseRunner 阶段事件回调(U8 由 backend 注入)
        skill_bundles: ``{phase: skill_bundle_text}`` —— 各阶段 cannbot skill 文本。
            缺省 ``{}`` 时各 LLM 节点用默认 Layer 6
        compile_node_factory: 自定义 compile 节点工厂(U13 注入真 compile_node);
            None 时用占位(返回 success=True)
        precision_node_factory: 自定义 precision 节点工厂(U13 注入);
            None 时用占位

    Returns:
        PhaseRunner —— invoke/resume 入口
    """
    factory = _build_phase_2_agent_factory(agent_factory)
    bundles = skill_bundles or {}

    # ---- LLM 节点(用 make_llm_node / make_hitl_llm_node) ----
    analyze_node = make_llm_node(
        phase="analyze",
        task_prompt_template=(
            "你是 Ascend C 算子架构师。请分析以下算子需求并产出 OpInfo 结构:\n\n"
            "用户需求:{user_input}\n\n"
            "输出格式:算子名称、描述、op_type、输入/输出 shape 与 dtype、"
            "migration_strategy=from_scratch。\n"
            "本阶段只产 OpInfo,不写代码。"
        ),
        skill_bundle_text=bundles.get("analyze"),
        agent_factory=factory,
    )

    def _design_payload_builder(state: dict, update: dict) -> dict:
        return {
            "phase": "design",
            "message": "请确认设计方案以继续 codegen 阶段",
            "design_doc": state.get("design_doc") or update.get("last_phase_result", {}),
            "options": ["approve", "reject"],
        }

    design_node = make_hitl_llm_node(
        phase="design",
        task_prompt_template=(
            "你是 Ascend C 算子架构师。基于 analyze 阶段的 OpInfo,产出 DESIGN.md 与 PLAN.md:\n\n"
            "OpInfo: {state}\n\n"
            "包含:Tiling 策略、API 映射、数据流、分支场景、文件清单、测试计划。"
        ),
        interrupt_payload_builder=_design_payload_builder,
        skill_bundle_text=bundles.get("design"),
        agent_factory=factory,
    )

    codegen_node = make_llm_node(
        phase="codegen",
        task_prompt_template=(
            "你是 Ascend C 算子 developer。基于已确认的 DESIGN.md 生成 AscendC kernel:\n\n"
            "{state}\n\n"
            "输出:kernel.cpp + op.cpp 文件内容。"
        ),
        skill_bundle_text=bundles.get("codegen"),
        agent_factory=factory,
    )

    review_fix_node = make_llm_node(
        phase="review_fix",
        task_prompt_template=(
            "你是 Ascend C 算子 reviewer。审查 codegen 阶段产出的代码:\n\n"
            "{state}\n\n"
            "输出:问题列表 + 修复建议(如有);无问题时返回 LGTM。"
        ),
        skill_bundle_text=bundles.get("review_fix"),
        agent_factory=factory,
    )

    # ---- 确定性节点:compile / precision(占位,U13 替换) ----
    if compile_node_factory is not None:
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
                "last_phase_result": {"phase": "compile", "placeholder": True},
            }
        compile_node = Node(name="compile", func=_placeholder_compile)

    if precision_node_factory is not None:
        precision_node = precision_node_factory()
    else:
        def _placeholder_precision(state: dict) -> dict:
            return {
                "precision_report": {
                    "operator_name": "placeholder",
                    "total_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                },
                "last_phase_result": {"phase": "precision", "placeholder": True},
            }
        precision_node = Node(name="precision", func=_placeholder_precision)

    def _done_node(state: dict) -> dict:
        return {"__status__": "done"}

    nodes = [
        Node(name="entry", func=lambda s: {}),
        analyze_node,
        design_node,
        codegen_node,
        review_fix_node,
        compile_node,
        precision_node,
        Node(name="done", func=_done_node),
    ]

    return PhaseRunner(nodes=nodes, store=store, phase_callback=phase_callback)
