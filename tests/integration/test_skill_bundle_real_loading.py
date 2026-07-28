"""cannbot skill 真实接入测试。

验证 ``build_new_dev_graph(use_real_skill_bundles=True)`` 时:

- 实际从 ``vendor/cannbot-skills`` 加载 skill(SKILL_BUNDLES 映射生效)
- ``render_skill_bundle_text`` 把 skill 列表渲染成 Layer 6 文本
- LLM 节点的 ``skills_layer_override`` 拿到非空字符串(含 skill name + description)
- 显式 skill_bundles 参数仍可 override 真实加载(优先级)

不调用真实 LLM,只校验 skill 文本注入是否正确。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ascend_op_agent.orchestrator import (
    CANNBOT_ROOT,
    CheckpointStore,
    build_new_dev_graph,
)
from ascend_op_agent.orchestrator.cannbot_loader import (
    build_skill_bundle,
    load_skill,
    render_skill_bundle_text,
)


# ---- build_skill_bundle + render_skill_bundle_text ----


def test_build_skill_bundle_returns_loaded_skills_for_design() -> None:
    """(new_dev, design) bundle 加载成功(ascendc-tiling-design 等)。"""
    skills = build_skill_bundle(phase="design", graph="new_dev")
    assert len(skills) > 0
    names = {s.name for s in skills}
    # SKILL_BUNDLES 里 (new_dev, design) 列了 3 个 skill
    assert "ascendc-tiling-design" in names or any("tiling-design" in n for n in names)


def test_render_skill_bundle_text_empty_returns_empty_string() -> None:
    """空 skill 列表 → 空串(便于调用方判断降级)。"""
    assert render_skill_bundle_text([]) == ""
    assert render_skill_bundle_text([], phase="design") == ""


def test_render_skill_bundle_text_contains_name_and_description() -> None:
    """渲染文本含每个 skill 的 name + description 首行。"""
    skills = build_skill_bundle(phase="design", graph="new_dev")
    text = render_skill_bundle_text(skills, phase="design")

    assert "## Available Skills (phase=design)" in text
    for s in skills:
        assert s.name in text
        desc_first = s.description.strip().split("\n", 1)[0]
        assert desc_first in text


def test_render_skill_bundle_text_no_phase_omits_label() -> None:
    """phase=None 时标题不显示 phase=。"""
    skills = build_skill_bundle(phase="design", graph="new_dev")
    text = render_skill_bundle_text(skills, phase=None)
    assert "## Available Skills\n" in text
    assert "phase=" not in text


def test_render_skill_bundle_text_truncates_multiline_description() -> None:
    """description 多行时只取首行(避免 Layer 6 膨胀)。"""
    from ascend_op_agent.orchestrator.cannbot_loader import CannbotSkill

    skill = CannbotSkill(
        name="test",
        description="first line\nsecond line\nthird line",
        body="",
        base_dir=Path("."),
    )
    text = render_skill_bundle_text([skill])
    assert "first line" in text
    assert "second line" not in text


# ---- build_new_dev_graph 真实接入 ----


class _FakeAgent:
    def __init__(self) -> None:
        self._h: list[dict] = []
        self.captured_overrides: list[Any] = []

        class _Mem:
            def __init__(self):
                self._p = {}

            def add(self, p, c):
                self._p.setdefault(p, []).append(c)

            def get(self, p):
                return list(self._p.get(p, []))

        self.memory = _Mem()

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
        no_tools=False,
    ):
        self.captured_overrides.append(skills_layer_override)
        self._h.append({"role": "user", "content": user_input})
        return "resp"


def test_use_real_skill_bundles_injects_nonempty_text_to_each_phase(tmp_path) -> None:
    """use_real_skill_bundles=True 时,各 LLM 节点 skills_layer_override 非空。"""
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent()
        agents.append(a)
        return a

    runner = build_new_dev_graph(
        store=store,
        agent_factory=factory,
        use_real_skill_bundles=True,
    )
    runner.invoke("design add", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    # 所有 LLM 阶段都应捕获到非空 override(analyze / design / codegen / review_fix)
    all_overrides: list[Any] = []
    for a in agents:
        all_overrides.extend(a.captured_overrides)

    assert len(all_overrides) >= 4
    for override in all_overrides:
        assert override is not None
        assert isinstance(override, str)
        assert "Available Skills" in override


def test_explicit_skill_bundles_overrides_real_loading(tmp_path) -> None:
    """显式 skill_bundles 参数优先级 > use_real_skill_bundles。"""
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent()
        agents.append(a)
        return a

    custom_text = "## CUSTOM OVERRIDE (test)"
    runner = build_new_dev_graph(
        store=store,
        agent_factory=factory,
        skill_bundles={"codegen": custom_text},
        use_real_skill_bundles=True,  # 同时开,但 codegen 应被 override
    )
    runner.invoke("design add", thread_id="t1")
    runner.resume("t1", payload={"approved": True})

    # 找到 codegen 阶段的 agent(第 3 个,索引 2)
    # agents 顺序:analyze, design, codegen, review_fix
    codegen_agent = agents[2] if len(agents) >= 3 else None
    assert codegen_agent is not None
    assert any(custom_text in (o or "") for o in codegen_agent.captured_overrides)


def test_default_no_real_bundles_uses_none(tmp_path) -> None:
    """use_real_skill_bundles=False 且无 skill_bundles:override 是 None。"""
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent()
        agents.append(a)
        return a

    runner = build_new_dev_graph(store=store, agent_factory=factory)
    runner.invoke("design add", thread_id="t1")

    # analyze 阶段 override 应是 None(走默认 Layer 6)
    all_overrides = [o for a in agents for o in a.captured_overrides]
    assert len(all_overrides) >= 1
    assert all(o is None for o in all_overrides)


def test_skill_bundle_text_contains_cannbot_skill_names(tmp_path) -> None:
    """真实加载的 skill 文本含 cannbot skill 名(如 ascendc-tiling-design)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    agents: list[_FakeAgent] = []

    def factory() -> _FakeAgent:
        a = _FakeAgent()
        agents.append(a)
        return a

    runner = build_new_dev_graph(
        store=store,
        agent_factory=factory,
        use_real_skill_bundles=True,
    )
    runner.invoke("design add", thread_id="t1")

    # analyze 阶段(agents[0])应拿到 design bundle(ascendc-tiling-design 等)
    analyze_overrides = agents[0].captured_overrides if agents else []
    non_empty = [o for o in analyze_overrides if o]
    assert len(non_empty) >= 1
    # 至少含一个 cannbot skill 名
    has_cannbot_skill_name = any("tiling-design" in o or "npu-arch" in o for o in non_empty)
    assert has_cannbot_skill_name, f"cannbot skill name missing in {non_empty}"


# ---- 仓库路径 sanity ----


def test_cannbot_root_exists() -> None:
    """vendor/cannbot-skills 存在(submodule 已拉取)。"""
    assert CANNBOT_ROOT.is_dir()
    assert (CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt" / "SKILL.md").is_file()
