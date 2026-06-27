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
    render_skill_bundle_text,
)
from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
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
            resolved[phase] = render_skill_bundle_text(skills, phase=phase)
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
    )

    # ---- codegen 节点:LLM 多文件写(默认) 或 scaffold 迁移(opt-in) ----
    if use_scaffold_codegen:
        # 参考工程迁移路径:910B 已有 /tmp/op_test(add_example,已验证可编译),
        # e2e_real_op.py 在 codegen 前 cp scaffold 到 working dir。
        # 本节点不调 LLM,只读 scaffold 关键文件填 code_result。
        def _scaffold_codegen_node(state: dict) -> dict:
            op_dir = "/tmp/e2e_ops_local/op_add"
            files: list[dict] = []
            key_files = [
                "CMakeLists.txt", "build.sh",
                "op_kernel/add_example_arch22.cpp",
                "op_kernel/arch22/add_example.h",
                "op_host/add_example_def.cpp",
                "op_host/add_example_infershape.cpp",
            ]
            for rel in key_files:
                full = Path(op_dir) / rel
                if full.exists():
                    files.append({
                        "path": str(full),
                        "content": full.read_text(encoding="utf-8"),
                        "tool": "scaffold_loaded",
                    })
            return {
                "code_result": {"files": files, "strategy": "reference_migration"},
                "last_phase_result": {
                    "phase": "codegen",
                    "strategy": "reference_migration",
                    "scaffold_path": op_dir,
                    "files_count": len(files),
                    "note": "scaffold from /tmp/e2e_scaffold (910B /tmp/op_test) — already a working elementwise add op",
                },
            }
        codegen_node = Node(name="codegen", func=_scaffold_codegen_node)
    else:
        # 默认:5 个独立 LLM 节点写 5 文件(成功率低,见 e2e 2026-06-25 记录)
        _codegen_files = [
            ("op_kernel.cpp", "AscendC kernel 实现(Init/Process 接口,必含 #include \"kernel_operator.h\")"),
            ("op_host.cpp", "tiling 函数 + shape 推导 + op 算子注册"),
            ("CMakeLists.txt", "含 ascendc target + include dirs + add_ops 子目录。**必须引用固定文件名 op_kernel.cpp + op_host.cpp,不要改名(如 add_custom.cpp)**。**ascendc.cmake 路径**:`${{ASCEND_TOOLKIT_HOME}}/aarch64-linux/tikcpp/ascendc_kernel_cmake/ascendc.cmake`"),
            ("build.sh", "bash 入口(先 source ${{ASCEND_TOOLKIT_HOME}}/set_env.sh,再 cmake -B build -DPKG ascend910b && cmake --build build -j 8,chmod +x)"),
            ("op_kernel.ini", "[opinfo] 段元信息(op_name / op_type 等)"),
        ]
        _phase_names = {
            "op_kernel.cpp": "codegen_kernel_cpp",
            "op_host.cpp": "codegen_host_cpp",
            "CMakeLists.txt": "codegen_cmakelists",
            "build.sh": "codegen_build_sh",
            "op_kernel.ini": "codegen_kernel_ini",
        }
        codegen_nodes = []
        for fname, desc in _codegen_files:
            all_files = ", ".join(f[0] for f in _codegen_files)
            codegen_nodes.append(make_llm_node(
                phase=_phase_names[fname],
                template_vars={"operator_dir": "/tmp/e2e_ops_local/op_add"},
                task_prompt_template=(
                    f"你是 Ascend C 算子 developer。基于已确认的 DESIGN.md 生成 1 个文件:\n\n"
                    f"{{state}}\n\n"
                    f"【当前文件】{fname} —— {desc}\n"
                    f"【绝对路径】{{operator_dir}}/{fname}\n\n"
                    f"【本工程所有文件名(固定)】{all_files}\n"
                    f"严禁改名(不要 add_custom.cpp / my_kernel.cpp)。\n\n"
                    f"【必须】调用 file_write 工具一次,参数:\n"
                    f"  path = {{operator_dir}}/{fname}\n"
                    f"  content = 完整文件内容\n\n"
                    f"【禁止】把代码贴在 assistant 文本(没用 file_write 工具视为失败)。\n"
                    f"写完后回 1 字符 'k'。"
                ),
                skill_bundle_text=bundles.get("codegen"),
                skill_names=bundle_names.get("codegen"),
                agent_factory=factory,
            ))
        # 把 5 个节点 wrap 成一个 node(用 codegen_aggregate 节点)
        # 简单做法:用第一个作为主 codegen,其他 4 个作为后续节点
        # 这里直接把所有 5 个都加入 nodes 列表
        codegen_node = codegen_nodes  # 实际上是 list

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
    nodes.extend([
        review_fix_node,
        compile_node,
        precision_node,
        delivery_mode_node,
        framework_adapt_node,
        Node(name="done", func=_done_node),
    ])

    return PhaseRunner(nodes=nodes, store=store, phase_callback=phase_callback)
