"""load_semantic_examples 单测:加载 add_example 8 语义文件供 codegen prompt 内联范本原文。

背景(2026-07 spike):LLM 凭"参考 add_example"文字描述 codegen 时幻觉 #include "vector_add_tiling.h"
(autogen 不生成,add_example 范本也不 include)。修复 = prompt 内联范本原文,LLM 照抄 include 清单。
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.cannbot_loader import load_semantic_examples


def test_load_semantic_examples_returns_three_phases():
    ex = load_semantic_examples()
    assert set(ex.keys()) == {"codegen_kernel", "codegen_host", "codegen_proto"}


def test_total_eight_semantic_files():
    """kernel 4 + host 3 + proto 1 = 8(arch22 一套,910B-only)。"""
    ex = load_semantic_examples()
    total = sum(len(v) for v in ex.values())
    assert total == 8


def test_codegen_host_def_cpp_only_includes_op_def_registry():
    """关键回归:add_example_def.cpp 只 include register/op_def_registry.h,不 include tiling.h。

    LLM 之前幻觉 vector_add_tiling.h include —— 范本本身不含,内联后 LLM 照抄不会幻觉。
    """
    ex = load_semantic_examples()
    host_files = dict(ex["codegen_host"])
    assert "op_host/add_example_def.cpp" in host_files
    def_cpp = host_files["op_host/add_example_def.cpp"]
    assert "register/op_def_registry.h" in def_cpp
    assert "tiling.h" not in def_cpp


def test_codegen_kernel_all_arch22():
    ex = load_semantic_examples()
    kernel_rels = [rel for rel, _ in ex["codegen_kernel"]]
    assert len(kernel_rels) == 4
    assert all("arch22" in r for r in kernel_rels)
