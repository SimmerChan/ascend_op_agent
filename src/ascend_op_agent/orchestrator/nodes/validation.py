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
    test_cases_resolver,
    operator_name_resolver=None,
    phase: str = "precision",
) -> Node:
    """构造真实 precision 节点(numpy diff)。

    Args:
        executor: NpuExecutor 实例
        test_cases_resolver: ``callable(state) -> list[dict]`` —— 从 state 抽
            test_cases(每个含 golden + actual)
        operator_name_resolver: ``callable(state) -> str`` —— 算 operator_name;
            None 时从 op_info.name 取,fallback "unknown"
        phase: 节点名

    Returns:
        Node —— 写 ``precision_report`` 字段
    """

    def _precision(state: dict) -> dict:
        test_cases = test_cases_resolver(state)
        if operator_name_resolver is not None:
            name = operator_name_resolver(state)
        else:
            name = (state.get("op_info") or {}).get("name", "unknown")
        report = executor.run_precision(name, test_cases)
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
