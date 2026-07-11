"""U13: 验证节点工厂(compile / precision 真节点 + fix_loop 接入)。

设计:

- ``make_real_compile_node``: 用 NpuExecutor 跑 cann_compile,产 compile_result
- ``make_real_precision_node``: 用 NpuExecutor 跑 numpy diff,产 precision_report
- ``make_compile_fix_node`` / ``make_precision_fix_node``: LLM 节点(scoped
  ascendc-crash-debug / ascendc-precision-debug skill)
- ``make_compile_fix_loop_node`` / ``make_precision_fix_loop_node``: U14 的
  make_fix_loop_node 包装,串 review→fix→re-review 循环

调用方通过 ``compile_node_factory=make_real_compile_node`` 等参数注入到
``build_new_dev_graph`` / ``build_migration_graph``,不破坏占位节点默认行为。
"""

from __future__ import annotations

from typing import Optional

from ascend_op_agent.orchestrator.fix_loop import make_fix_loop_node
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.npu_exec import NpuExecutor
from ascend_op_agent.orchestrator.state_machine import Node


# ---- compile 节点 ----


def make_real_compile_node(
    executor: NpuExecutor,
    operator_path_resolver,
    phase: str = "compile",
) -> Node:
    """构造真实 compile 节点(用 NpuExecutor 跑 cann_compile)。

    Args:
        executor: NpuExecutor 实例
        operator_path_resolver: ``callable(state) -> str`` —— 从 state 算出
            operator_path(如从 code_result.files 提取目录)
        phase: 节点名

    Returns:
        Node —— 写 ``compile_result`` 字段
    """

    def _compile(state: dict) -> dict:
        operator_path = operator_path_resolver(state)
        result = executor.compile_to_dict(operator_path)
        return {"compile_result": result}

    return Node(name=phase, func=_compile)


# ---- precision 节点 ----


def make_real_precision_node(
    executor: NpuExecutor,
    operator_path_resolver,
    operator_name_resolver=None,
    phase: str = "precision",
    test_cases_resolver=None,  # legacy: deprecated, kept for backward compat
) -> Node:
    """构造真实 precision 节点(U4:用 NpuExecutor.run_st_driver 跑 ST 驱动)。

    6 步配方由 ST 驱动内置做(910B NPU 跑 + CPU golden + MERE/MARE 比对,
    见 2026-06-27 spike 报告),节点只需提供 operator_path 即可。

    Args:
        executor: NpuExecutor 实例
        operator_path_resolver: ``callable(state) -> str`` —— 算子工程根目录
            (含 build/custom_opp_*.run + tests/st/)
        operator_name_resolver: ``callable(state) -> str`` —— 算子名(决定
            vendors 目录 + ST 二进制名);None 时从 op_info.name 取,fallback "unknown"
        phase: 节点名
        test_cases_resolver: **deprecated**,保留仅为向后兼容(老测试用)。
            新代码不要传 —— ST 驱动自己定义 case。

    Returns:
        Node —— 写 ``precision_report`` 字段(run_st_driver 返回的同形 dict)
    """

    def _precision(state: dict) -> dict:
        operator_path = operator_path_resolver(state)
        if operator_name_resolver is not None:
            name = operator_name_resolver(state)
        else:
            name = (state.get("op_info") or {}).get("name", "unknown")
        # Gate: 编译必须成功才跑(否则 NPU run 无意义)
        compile_res = state.get("compile_result") or {}
        if not compile_res.get("success", False):
            return {
                "precision_report": {
                    "operator_name": name,
                    "total_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                    "cases": [],
                    "success": False,
                    "error": "compile_not_ready (run_st_driver 跳过)",
                }
            }
        # U4: 调 run_st_driver(NPU 跑 + CPU golden + MERE/MARE 内置)
        report = executor.run_st_driver(operator_path, op_name=name)
        return {"precision_report": report}

    return Node(name=phase, func=_precision)


# ---- fix 节点(LLM) ----


def make_compile_fix_node(
    agent_factory: AgentFactory,
    skill_bundle_text: Optional[str] = None,
    phase: str = "compile_fix",
) -> Node:
    """LLM 节点:基于 compile_result.stderr 修复代码(scoped ascendc-crash-debug)。"""
    return make_llm_node(
        phase=phase,
        task_prompt_template=(
            "你是 Ascend C 编译错误修复专家。基于 compile_result.stderr 分析错误,"
            "产出修复后的 kernel.cpp + op.cpp:\n\n"
            "状态(compile_result + code_result + 历史):\n{state}\n\n"
            "重点:语法错误、缺 header、API 误用、dtype 不匹配。\n"
            "输出修复后的完整代码;不可修复时明确说明原因。"
        ),
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )


def make_precision_fix_node(
    agent_factory: AgentFactory,
    skill_bundle_text: Optional[str] = None,
    phase: str = "precision_fix",
) -> Node:
    """LLM 节点:基于 precision_report 失败 case 修复(scoped ascendc-precision-debug)。"""
    return make_llm_node(
        phase=phase,
        task_prompt_template=(
            "你是 Ascend C 精度修复专家。基于 precision_report 失败 case 分析,"
            "产出修复后的 kernel.cpp + op.cpp:\n\n"
            "状态(precision_report + code_result + 历史):\n{state}\n\n"
            "重点:数值溢出、reduce 顺序、cast 边界、累加精度。\n"
            "输出修复后的完整代码;不可修复时明确说明原因(如缺 dtype 支持)。"
        ),
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )


# ---- fix_loop 节点 ----


def make_compile_fix_loop_node(
    review_node: Node,
    fix_node: Node,
    max_rounds: int = 3,
    phase: str = "compile_fix_loop",
) -> Node:
    """compile fix_loop 节点(U14 make_fix_loop_node 包装)。"""
    return make_fix_loop_node(
        phase=phase,
        kind="compile",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=max_rounds,
    )


def make_precision_fix_loop_node(
    review_node: Node,
    fix_node: Node,
    max_rounds: int = 3,
    phase: str = "precision_fix_loop",
) -> Node:
    """precision fix_loop 节点。"""
    return make_fix_loop_node(
        phase=phase,
        kind="precision",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=max_rounds,
    )


# ---- U2 compile fix_loop(内嵌 re-compile,U1 spike #9 后新增)----


def make_real_compile_fix_loop_node(
    executor: NpuExecutor,
    operator_path_resolver,
    agent_factory: AgentFactory,
    max_rounds: int = 3,
    skill_bundle_text: Optional[str] = None,
    phase: str = "compile_fix_loop",
) -> Node:
    """compile + fix 闭环(内嵌 re-compile)。

    区别于 ``make_compile_fix_loop_node``(review→fix→re-review,不含 re-compile):
    本节点每轮真跑 compile,失败时 LLM 修构建文件(markdown 落盘),re-compile。

    U1 spike #9 暴露:LLM 单次 codegen 写不对 build.sh/CMakeLists(漏 -j* case /
    ASCEND_COMPUTE_UNIT / 环境变量)。本节点通过多轮 compile→fix→re-compile 收敛。

    每轮:
      1. ``operator_path_resolver(state)`` → compile → compile_result
      2. success → done
      3. fail → fix_node(make_llm_node,看 stderr 修构建文件,markdown 自动落盘)
      4. apply_update(code_result.files 更新)→ re-compile

    Args:
        executor: NpuExecutor(SSH→910B build.sh)
        operator_path_resolver: ``callable(state) -> str`` 算子工程根目录
        agent_factory: LLM agent 工厂(修复用)
        max_rounds: 最多几轮 compile→fix
        skill_bundle_text: 修复阶段 skill 文本(ascendc-crash-debug)
        phase: 节点名

    Returns:
        Node —— 写 ``compile_result`` + ``{phase}_result``(status/rounds/reason)
    """
    from ascend_op_agent.orchestrator.state_machine import apply_update

    # fix 节点:看 compile_result.stderr,引导修构建文件(build.sh/CMakeLists)
    # + kernel。make_llm_node 自动 markdown 提取 + 落盘(common.py markdown fallback)。
    fix_node = make_llm_node(
        phase=f"{phase}_fix",
        task_prompt_template=(
            "你是 Ascend C 编译错误修复专家。上次编译失败,stderr 见 state.compile_result。\n\n"
            "完整状态(compile_result + code_result.files + 历史):\n{state}\n\n"
            "【诊断错误类型】\n"
            "- 构建配置错误:build.sh 参数解析(漏 -j* / --soc case)、CMakeLists.txt"
            "(ASCEND_COMPUTE_UNIT / ascendc.cmake include / SOC_VERSION)、"
            "环境变量(ASCEND_CANN_PACKAGE_PATH / ASCEND_TOOLKIT_HOME)\n"
            "- kernel 代码错误:语法、header、API 误用、dtype\n\n"
            "【修复】修对应文件,**只输出修后的文件**(markdown 代码块,路径在首行 "
            "`// /path` 或 `# /path` 注释)。构建错误优先修 build.sh / CMakeLists.txt。"
            "参考 add_example 工程的正确构建配置(ASCEND_COMPUTE_UNIT 分代 arch22/arch35)。"
        ),
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )

    def _loop(state: dict) -> dict:
        rounds = 0
        last_result: dict = {}
        while rounds < max_rounds:
            rounds += 1
            # 1. compile
            operator_path = operator_path_resolver(state)
            last_result = executor.compile_to_dict(operator_path)
            state["compile_result"] = last_result  # 让 fix_node 看到 stderr

            if last_result.get("success"):
                return {
                    "compile_result": last_result,
                    f"{phase}_result": {
                        "status": "done",
                        "rounds": rounds,
                        "reason": "clean",
                    },
                }

            # 2. 失败:若还有轮次,fix(LLM 修构建文件 → markdown 落盘)→ re-compile
            if rounds >= max_rounds:
                break
            update = fix_node.func(state)
            apply_update(state, update)  # code_result.files 更新 + 磁盘落盘

        return {
            "compile_result": last_result,
            f"{phase}_result": {
                "status": "failed",
                "rounds": rounds,
                "reason": "max_rounds",
            },
        }

    return Node(name=phase, func=_loop)


# ---- 便捷 resolver(从 state 提取 operator_path / test_cases) ----


def operator_path_from_code_result(state: dict) -> str:
    """默认 resolver:从 code_result.files[0].path 提取算子目录。"""
    code_result = state.get("code_result") or {}
    files = code_result.get("files") or []
    if not files:
        return ""
    first = files[0]
    if isinstance(first, dict):
        path = first.get("path") or first.get("dir") or ""
        # 取目录(算子通常是目录形式)
        return str(path)
    return str(first)


def test_cases_from_state(state: dict) -> list[dict]:
    """默认 resolver:从 state['test_cases'] 取(U13 注入或前端传入)。"""
    return list(state.get("test_cases") or [])
