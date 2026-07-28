"""Path-C 新开发图(analyze → design → codegen → review_fix → compile → precision)。

P0 MVP 版本(U9):

- 6 个 LLM 节点 + 1 个 done,顺序执行
- design 节点是 HITL(LLM 跑完 → 请求用户批准 → resume 推进)
- compile / compile_fix / precision 在 U9 阶段先用占位节点(返回 success);
  U13 加 NpuExecutor 后替换为真实 cann_compile 调用
- review_fix 在 U9 阶段无条件推进到 compile;U14 加 fix_loop 后补 retry 边

cannbot skill 绑定(决策 3 路径 C 行,真实接入由 cannbot_loader 提供):

- analyze/design: ascendc-tiling-design + ascendc-simt-tiling-design + npu-arch
- codegen: ascendc-direct-invoke-template + ascendc-simt-best-practices
- review_fix: ascendc-code-review
- compile: 无 LLM,确定性 cann_compile 调用(U13)
- precision: ascendc-ops-precision-standard skill 集(numpy diff,U13)

调用方传入 ``use_real_skill_bundles=True`` 时,自动从 cannbot submodule 加载
skill 并渲染成 Layer 6 文本;否则用 ``skill_bundles`` 显式参数(便于测试)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ascend_op_agent.orchestrator.cannbot_loader import (
    build_skill_bundle,
    load_semantic_examples,
    render_skill_bundle_text,
)
from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node, to_pascal
from ascend_op_agent.orchestrator.nodes.delivery import (
    make_delivery_mode_node,
    make_framework_adapt_node,
)
from ascend_op_agent.orchestrator.nodes.hitl import make_hitl_llm_node
from ascend_op_agent.orchestrator.state_machine import Node, PhaseCallback, PhaseRunner


# (graph, phase) → 该阶段要加载的 cannbot skill bundle
# 对应 cannbot_loader.SKILL_BUNDLES 的 (new_dev, *) 行
_NEW_DEV_PHASE_TO_BUNDLE_KEY: dict[str, tuple[str, str]] = {
    "analyze": ("new_dev", "design"),  # analyze 与 design 共用 architect skill 集
    "design": ("new_dev", "design"),
    "codegen": ("new_dev", "codegen"),
    "review_fix": ("new_dev", "review"),
}


def _build_phase_2_agent_factory(
    agent_factory: Optional[AgentFactory],
) -> AgentFactory:
    if agent_factory is None:
        raise ValueError(
            "agent_factory required (production wires AIAgent with session_manager=None)"
        )
    return agent_factory


def _resolve_skill_bundles(
    skill_bundles: Optional[dict[str, str]],
    use_real_skill_bundles: bool,
) -> tuple[dict[str, Optional[str]], dict[str, list[str]]]:
    """合并显式 skill_bundles 参数和真实 cannbot 加载。

    优先级:显式参数 > 真实加载。允许调用方对单阶段 override。

    Returns:
        (bundle_texts, bundle_names) —— 文本注入 LLM Layer 6;名字供 U2
        SkillUsageRegistry 跟踪(只有真实加载的 bundle 有名字,显式 override 无)。
    """
    resolved: dict[str, Optional[str]] = {
        "analyze": None,
        "design": None,
        "codegen": None,
        "review_fix": None,
    }
    names: dict[str, list[str]] = {}

    if use_real_skill_bundles:
        for phase, key in _NEW_DEV_PHASE_TO_BUNDLE_KEY.items():
            skills = build_skill_bundle(phase=key[1], graph=key[0])
            # U2: codegen 阶段内联 add_custom 构建参考(含 CANN 环境配置),
            # 解决 LLM 写 build.sh/CMakeLists.txt 漏 ASCEND_CANN_PACKAGE_PATH
            inline_build = phase == "codegen"
            resolved[phase] = render_skill_bundle_text(
                skills, phase=phase, inline_build_template=inline_build
            )
            names[phase] = [s.name for s in skills]

    # 显式 override(覆盖文本,但名字保留真实加载的 —— 显式文本无名字可提取)
    if skill_bundles:
        for phase, text in skill_bundles.items():
            resolved[phase] = text

    return resolved, names


def build_new_dev_graph(
    store: CheckpointStore,
    agent_factory: Optional[AgentFactory] = None,
    phase_callback: Optional[PhaseCallback] = None,
    skill_bundles: Optional[dict[str, str]] = None,
    use_real_skill_bundles: bool = False,
    compile_node_factory: Optional[Callable[[], Node]] = None,
    precision_node_factory: Optional[Callable[[], Node]] = None,
    use_scaffold_codegen: bool = False,
    compile_fix_loop_node_factory: Optional[Callable[[], Node]] = None,
    precision_fix_loop_node_factory: Optional[Callable[[], Node]] = None,
) -> PhaseRunner:
    """构造 Path-C 新开发图。

    Args:
        store: CheckpointStore 实例
        agent_factory: 返回 fresh AIAgent 的工厂
        phase_callback: PhaseRunner 阶段事件回调(U8 由 backend 注入)
        skill_bundles: ``{phase: skill_bundle_text}`` —— 显式覆盖各阶段 skill 文本。
            优先级高于 ``use_real_skill_bundles``
        use_real_skill_bundles: True 时从 cannbot submodule 加载真实 skill。
            生产环境应设 True;测试默认 False(用 mock)
        compile_node_factory: 自定义 compile 节点工厂(U13 注入真 compile_node);
            None 时用占位(返回 success=True)
        precision_node_factory: 自定义 precision 节点工厂(U13 注入);
            None 时用占位
        compile_fix_loop_node_factory: U3 注入真 compile fix_loop 包装
            (review_node 检查 compile_result,fix_node 调 LLM 修);非 None 时
            替代单 compile_node 接入图。None 保持单节点行为(向后兼容)
        precision_fix_loop_node_factory: 同上,precision fix_loop 包装替代
            precision_node

    Returns:
        PhaseRunner —— invoke/resume 入口
    """
    factory = _build_phase_2_agent_factory(agent_factory)
    bundles, bundle_names = _resolve_skill_bundles(skill_bundles, use_real_skill_bundles)

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
        skill_names=bundle_names.get("analyze"),
        agent_factory=factory,
        no_tools=True,  # analyze 期望文本 OpInfo 输出,不 tool calling(否则推理模型 MAX_ITER 不产出)
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
        skill_names=bundle_names.get("design"),
        agent_factory=factory,
        no_tools=True,  # design 期望文本 DESIGN.md 输出,不 tool calling(否则 MAX_ITER 致 design_doc=None 拖累 codegen)
    )

    # ---- codegen 节点:LLM 多文件写(默认) 或 scaffold 迁移(opt-in) ----
    # U4 方向 B:codegen = scaffold 注入构建文件(不经 LLM) + LLM 写语义(子目录)。
    # 构建文件用 add_example 原版参数化注入(消除 ascendc_add_ops 幻觉 + 保证
    # npu_op_package 子目录结构完整,binary target 不缺),LLM 只写 kernel/host 语义(arch22)。
    _OPERATOR_DIR = "/tmp/e2e_ops_local/op_add"

    def _scaffold_inject_node(state: dict) -> dict:
        op_info = state.get("op_info") or {}
        op_snake = op_info.get("name") or "op_add"
        op_pascal = op_info.get("class_name") or to_pascal(op_snake)
        from ascend_op_agent.orchestrator.cannbot_loader import load_build_scaffold

        scaffold = load_build_scaffold(op_snake, op_pascal)
        files: list[dict] = []
        for rel, content in scaffold.items():
            full = Path(_OPERATOR_DIR) / rel
            files.append({"path": str(full), "content": content, "tool": "scaffold_loaded"})
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content, encoding="utf-8")
        return {
            "code_result": {"files": files, "strategy": "scaffold_injected"},
            "last_phase_result": {
                "phase": "codegen_scaffold",
                "files_count": len(files),
                "op_snake": op_snake,
                "op_pascal": op_pascal,
            },
        }

    scaffold_node = Node(name="codegen_scaffold", func=_scaffold_inject_node)

    # LLM 语义节点(3,子目录 arch22,每节点多 markdown 代码块)。
    # op 名从 state.op_info 取(LLM 在 prompt 里看 state.op_info.name/class_name)。
    # <op_snake> 是字面占位(非 format {} 占位,避免 make_llm_node KeyError fallback),
    # LLM 按 state.op_info.name 替换;{state} 是 make_llm_node format 占位。
    _semantic_specs = [
        (
            "codegen_kernel",
            "kernel 实现(910B arch22):entry + kernel class + tiling data/key",
            (
                "op_kernel/<op_snake>_arch22.cpp",
                "op_kernel/arch22/<op_snake>.h",
                "op_kernel/arch22/<op_snake>_tiling_data.h",
                "op_kernel/arch22/<op_snake>_tiling_key.h",
            ),
            "参考 add_example 的 op_kernel/add_example_arch22.cpp(entry __global__ void add_example) + arch22/add_example.h(class NsAddExample::AddExample,Init/Process/CopyIn/Compute/CopyOut) + tiling_data.h(struct) + tiling_key.h(ASCENDC_TPL_ARGS_DECL)。按算子语义改写。",
        ),
        (
            "codegen_host",
            "host 实现:def + infershape + tiling(arch22)",
            (
                "op_host/<op_snake>_def.cpp",
                "op_host/<op_snake>_infershape.cpp",
                "op_host/arch22/<op_snake>_tiling.cpp",
            ),
            "参考 add_example 的 op_host/add_example_def.cpp(class AddExample : public OpDef + OP_ADD) + infershape.cpp(IMPL_OP_INFERSHAPE) + arch22/add_example_tiling.cpp(IMPL_OP_OPTILING + TilingFunc)。按算子语义改写。",
        ),
        (
            "codegen_proto",
            "graph proto 注册",
            ("op_graph/<op_snake>_proto.h",),
            "参考 add_example/op_graph/add_example_proto.h(REG_OP(AddExample).INPUT.INPUT.OUTPUT)。按算子语义改写。",
        ),
    ]
    _semantic_examples = load_semantic_examples()
    codegen_nodes = [scaffold_node]
    for _phase, _desc, _rels, _guide in _semantic_specs:
        _rels_str = "\n".join(f"- {_OPERATOR_DIR}/{r}" for r in _rels)
        _ex_files = _semantic_examples.get(_phase, [])
        _ex_str = "\n\n".join(
            f"```cpp\n// add_example 范本 {rel}\n{content}\n```" for rel, content in _ex_files
        )
        codegen_nodes.append(
            make_llm_node(
                phase=_phase,
                task_prompt_template=(
                    f"你是 Ascend C 算子 developer。基于 DESIGN.md + state.op_info 生成 {_desc}。\n\n"
                    "完整状态(含 op_info):\n{state}\n\n"
                    "【算子名】从 state.op_info.name 取 snake_case(如 vector_add)命名文件/函数,"
                    "state.op_info.class_name 取 PascalCase(如 VectorAdd)命名类。"
                    "下方文件路径里的 <op_snake> 占位用 state.op_info.name 替换。\n\n"
                    f"【要生成的文件(子目录绝对路径,每文件一个 markdown 代码块)】\n{_rels_str}\n\n"
                    f"【范本原文(add_example 真实可编译工程,严格照此 include 清单 + 宏结构 + API;"
                    f"把 add_example/AddExample 替换为 state.op_info.name/class_name,按算子语义改写)】\n{_ex_str}\n\n"
                    "【禁令 —— 必须遵守】\n"
                    "- 严格照范本 include 清单,#include 只能是范本中存在的 header 或工程内已生成的文件;"
                    "禁止幻觉 autogen 生成的 header(实测 *_tiling.h 不存在 —— add_example 范本 def.cpp"
                    " 只 #include register/op_def_registry.h,autogen 不生成 tiling.h)\n\n"
                    "【输出格式】每个文件一个 markdown 代码块,首行路径注释 // 绝对路径:\n"
                    "```cpp\n// /abs/path/file\n<内容>\n```\n"
                    "【禁止】调用 file_write 等 tool(本节点加 markdown fallback 提取多代码块)。\n"
                    "只输出 markdown 代码块(不要解释)。"
                ),
                skill_bundle_text=bundles.get("codegen"),
                skill_names=bundle_names.get("codegen"),
                agent_factory=factory,
                no_tools=True,  # U5:codegen 语义节点禁用 tool calling(LLM 只输出 markdown,避免 max_iterations 循环)
            )
        )
    codegen_node = codegen_nodes  # list:[scaffold_inject, kernel, host, proto]

    review_fix_node = make_llm_node(
        phase="review_fix",
        task_prompt_template=(
            "你是 Ascend C 算子 reviewer。审查 codegen 阶段产出的代码:\n\n"
            "{state}\n\n"
            "输出:问题列表 + 修复建议(如有);无问题时返回 LGTM。"
        ),
        skill_bundle_text=bundles.get("review_fix"),
        skill_names=bundle_names.get("review_fix"),
        agent_factory=factory,
        no_tools=True,  # review_fix 期望文本审查报告,不 tool calling
    )

    # ---- 确定性节点:compile / precision(占位,U13 替换) ----
    # U3: 优先用 fix_loop 包装节点(review+fix 闭环,跨轮 messages 走 reducer 不丢历史);
    #     退而用单节点(compile_node_factory / precision_node_factory);
    #     最后用占位
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
                "last_phase_result": {"phase": "compile", "placeholder": True},
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
                    "operator_name": "placeholder",
                    "total_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                },
                "last_phase_result": {"phase": "precision", "placeholder": True},
            }

        precision_node = Node(name="precision", func=_placeholder_precision)

    # ---- 交付模式(U12)----
    delivery_mode_node = make_delivery_mode_node(phase="delivery_mode")
    framework_adapt_node = make_framework_adapt_node(
        phase="framework_adapt",
        agent_factory=factory,
        skill_bundle_text=bundles.get("framework_adapt"),
    )

    def _done_node(state: dict) -> dict:
        return {"__status__": "done"}

    nodes = [
        Node(name="entry", func=lambda s: {}),
        analyze_node,
        design_node,
    ]
    if isinstance(codegen_node, list):
        # LLM-based:5 个独立 codegen 节点
        nodes.extend(codegen_node)
    else:
        # scaffold-based:单 codegen 节点
        nodes.append(codegen_node)
    nodes.extend(
        [
            review_fix_node,
            compile_node,
            precision_node,
            delivery_mode_node,
            framework_adapt_node,
            Node(name="done", func=_done_node),
        ]
    )

    return PhaseRunner(nodes=nodes, store=store, phase_callback=phase_callback)
