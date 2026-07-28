"""U3:_render_build_template_section 解耦查找 + codegen build.sh prompt 改范式单测。

覆盖:
- L4:_render_build_template_section 显式定位 add_example,返非空且含 npu_op_package 与 -j*) 锚点
- L4:渲染含 CMakeLists.txt + build.sh 两段 fenced
- L5:codegen build.sh desc 不含直连 cmake -j 8 错误先验
- L5:codegen build.sh desc 引用 npu_op_package 范式
- KTD3:codegen SKILL_BUNDLES 不变(不把 registry-invoke-template 加进去)
"""

from __future__ import annotations

import pathlib

from ascend_op_agent.orchestrator.cannbot_loader import (
    SKILL_BUNDLES,
    _render_build_template_section,
)


# ---- L4:显式定位 add_example,返非空含锚点 ----


def test_render_build_template_returns_nonempty_with_anchors():
    """不传 skills 也能渲染(新实现不依赖入参)。原遍历 skills 在 codegen bundle 下
    永远找不到 add_example → 返空串(死代码 bug)。"""
    out = _render_build_template_section([])
    assert out, "应返非空(原 codegen 阶段因 bundle 不匹配返空是 L4 死代码 bug)"
    # 910B CANN 9.1.0 真实构建范式锚点
    assert "npu_op_package(" in out, "应含 add_example CMakeLists 的 npu_op_package 宏"
    assert "-j*)" in out, "应含 add_example build.sh 的 -j*) case"


def test_render_build_template_contains_cmakelists_and_buildsh():
    out = _render_build_template_section([])
    assert "### CMakeLists.txt" in out
    assert "### build.sh" in out
    assert "```cmake" in out
    assert "```bash" in out


# ---- L5:codegen build.sh prompt 不再教直连 cmake -j 8 错误先验 ----


def test_codegen_build_sh_prompt_no_direct_cmake_j8():
    src = pathlib.Path("src/ascend_op_agent/orchestrator/graphs/new_dev.py").read_text(
        encoding="utf-8"
    )
    assert "cmake --build build -j 8" not in src, (
        "L5:codegen build.sh desc 不应再教直连 cmake -j 8 范式"
        "(spike 暴露的错误先验,与 add_example npu_op_package 范式相反)"
    )


def test_codegen_build_sh_prompt_references_npu_op_package():
    src = pathlib.Path("src/ascend_op_agent/orchestrator/graphs/new_dev.py").read_text(
        encoding="utf-8"
    )
    assert "npu_op_package" in src, "L5:codegen build.sh desc 应引用 npu_op_package 范式"


# ---- KTD3:codegen SKILL_BUNDLES 不变(不把 registry-invoke-template 加进去) ----


def test_codegen_skill_bundles_unchanged():
    """不把 ascendc-registry-invoke-template 加进 codegen bundle(避免 token 增量,
    add_example 是构建参考工程,与 codegen skill 简介是不同语义层)。"""
    codegen_bundle = SKILL_BUNDLES.get(("new_dev", "codegen"), [])
    paths = " ".join(codegen_bundle)
    assert "ascendc-registry-invoke-template" not in paths, (
        "KTD3:cannbot_loader SKILL_BUNDLES[('new_dev','codegen')] 不应含 "
        "ascendc-registry-invoke-template(已改为显式定位 add_example)"
    )
    # 原 codegen bundle 成员保留
    assert any("ascendc-direct-invoke-template" in p for p in codegen_bundle)
