# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")
"""Tests for PR-B U3: Layer 6 R5b 路由命中(task_type + recent_loads, cap=10)
+ R7 分组渲染(cannbot 前 / self-built 后, 标题区分).

Approach:
  - 直接 patch PromptBuilder._load_self_built_skills 返回 fake CannbotSkill 列表
    (避免文件 I/O);fake CannbotSkill 带 frontmatter["ascend_op_agent"]["task_type"].
  - cover:
      R5b:
        - task_type 命中(只返回该 task_type 的 self-built)
        - recent_loads 补充(task_type 命中 + 加最近 load 过的 name)
        - 无 task_type 无 recent_loads → 全保留 (PR-A 行为, 兜底)
        - cap=10(>10 时截断)
        - recent_loads 去重 / 顺序保留
      R7:
        - cannbot + self-built 都存在 → cannbot 在前, self-built (with 标题) 在后
        - 只有 self-built → 标题替换成 "(self-built)"
        - 只有 cannbot override → 原 override 不变
        - 都空 → "暂无自研 skill 沉淀" fallback
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ascend_op_agent.agent.prompt_builder import (
    HERMES_LAYER_LIMIT,
    RECENT_LOADS_MAX,
    PromptBuilder,
)
from ascend_op_agent.orchestrator.cannbot_loader import CannbotSkill


# ---- helpers --------------------------------------------------------------


def _fake_skill(name: str, *, task_type: str = "", topic: str = "") -> CannbotSkill:
    """Build a minimal CannbotSkill with frontmatter → ascend_op_agent nested.

    IMPORTANT: frontmatter shape mirrors SkillStorage.load_skill path:
    ``loaded.metadata`` returns the raw ``metadata: { ascend_op_agent: {...} }``
    dict from frontmatter (NOT re-wrapped under "metadata:" key). CannbotSkill
    then gets ``frontmatter = loaded.metadata`` → ``fm["ascend_op_agent"]`` is
    the right path PromptBuilder._self_built_task_type reads.
    """
    return CannbotSkill(
        name=name,
        description=f"description for {name}",
        body="",
        base_dir=Path("/tmp/_fake"),
        frontmatter={
            "ascend_op_agent": {
                "task_type": task_type,
                "topic": topic,
            },
        },
    )


def _skill_lines(out: str) -> list[str]:
    """Extract rendered self-built skill name lines ("- **NAME**: ...") from output."""
    import re

    return re.findall(r"- \*\*([\w-]+)\*\*:", out)


@pytest.fixture
def pb(monkeypatch):
    """PromptBuilder with monkeypatched _load_self_built_skills(由 per-test 改写).
    Default: 返回 [] (空 self-built).
    """
    p = PromptBuilder()
    monkeypatch.setattr(p, "_load_self_built_skills", lambda storage: [])
    return p


@pytest.fixture
def override_cannbot():
    return (
        "## Available Skills (phase=design)\n\n"
        "- **ascendc-tiling-design**: Tiling API patterns.\n"
    )


# ---- R5b 路由命中 ---------------------------------------------------------


def test_r5b_task_type_filters_self_built(pb, monkeypatch, override_cannbot):
    """task_type=develop → 只返回 task_type=develop 的 self-built."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("dev-tiling", task_type="develop"),
            _fake_skill("dev-runtime", task_type="develop"),
            _fake_skill("mig-cuda", task_type="migrate"),
        ],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type="develop",
    )
    lines = _skill_lines(out)
    assert "dev-tiling" in lines
    assert "dev-runtime" in lines
    assert "mig-cuda" not in lines
    # cannbot (override) 在前
    assert out.index("## Available Skills (phase=design)") < out.index(
        "## Available Skills (self-built)"
    )


def test_r5b_no_task_type_no_recent_loads_returns_all(pb, monkeypatch, override_cannbot):
    """无 task_type 无 recent_loads → 兜底返回全部 self-built (PR-A 兼容)."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("a", task_type="develop"),
            _fake_skill("b", task_type="migrate"),
        ],
    )
    out = pb._build_skills_layer(override=override_cannbot)
    lines = _skill_lines(out)
    assert "a" in lines and "b" in lines


def test_r5b_recent_loads_supplements_when_task_type_set(pb, monkeypatch, override_cannbot):
    """task_type=develop + recent_loads=['mig-x'] → task_type 命中 + recent_loads 补."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("dev-tiling", task_type="develop"),
            _fake_skill("mig-x", task_type="migrate"),  # 被 recent_loads 拉进来
            _fake_skill("mig-y", task_type="migrate"),  # 保持在外
        ],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type="develop",
        recent_loads=["mig-x"],
    )
    lines = _skill_lines(out)
    assert "dev-tiling" in lines
    assert "mig-x" in lines  # recent_loads 拉进来
    assert "mig-y" not in lines  # 不在 recent_loads 也没 task_type 命中


def test_r5b_recent_loads_dedupes(pb, monkeypatch, override_cannbot):
    """recent_loads 含已有 task_type 命中的 skill → 去重不重复."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("dev-tiling", task_type="develop"),
        ],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type="develop",
        recent_loads=["dev-tiling", "dev-tiling", "mig-x"],
    )
    lines = _skill_lines(out)
    assert lines.count("dev-tiling") == 1
    # dev-tiling 只出现一次 (不抛错)


def test_r5b_recent_loads_only(pb, monkeypatch, override_cannbot):
    """task_type=None + recent_loads=['a','b'] → 只 recent_loads 中的."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("a"),
            _fake_skill("b"),
            _fake_skill("c"),
        ],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type=None,
        recent_loads=["a", "b"],
    )
    lines = _skill_lines(out)
    assert "a" in lines and "b" in lines
    assert "c" not in lines


def test_r5b_recent_loads_capped_to_MAX(pb, monkeypatch, override_cannbot):
    """recent_loads 超过 RECENT_LOADS_MAX=5 → 只用前 5 个."""
    monkeypatch.setattr(
        pb, "_load_self_built_skills", lambda storage: [_fake_skill(f"s{i}") for i in range(10)]
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        recent_loads=[f"s{i}" for i in range(10)],
    )
    lines = _skill_lines(out)
    # 前 5 个(s0-s4)必须在; 6-9 仍在 HERMES cap=10 中,但 recent_loads 只前 5 个
    for i in range(5):
        assert f"s{i}" in lines
    for i in range(5, 10):
        assert f"s{i}" not in lines
    assert RECENT_LOADS_MAX == 5


def test_r5b_cap_10(pb, monkeypatch, override_cannbot):
    """15 个 self-built → cap 到 HERMES_LAYER_LIMIT=10."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [_fake_skill(f"skill-{i}", task_type="develop") for i in range(15)],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type="develop",
    )
    # 渲染的 self-built 应只有 10 行
    lines = _skill_lines(out)
    self_built_lines = [n for n in lines if n.startswith("skill-")]
    assert len(self_built_lines) == 10
    # 前 10 个保留,11~15 丢弃
    for i in range(10):
        assert f"skill-{i}" in self_built_lines
    for i in range(10, 15):
        assert f"skill-{i}" not in self_built_lines
    assert HERMES_LAYER_LIMIT == 10


# ---- R7 分组渲染 ---------------------------------------------------------


def test_r7_both_groups_have_distinct_titles(pb, monkeypatch, override_cannbot):
    """canbot (override) 在前 + self-built (标题含 "(self-built)") 在后."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("dev-tiling", task_type="develop"),
        ],
    )
    out = pb._build_skills_layer(
        override=override_cannbot,
        task_type="develop",
    )
    assert "## Available Skills (phase=design)" in out  # cannbot phase 标题
    assert "## Available Skills (self-built)" in out  # self-built 标题
    # cannbot 在前
    assert out.index("## Available Skills (phase=design)") < out.index(
        "## Available Skills (self-built)"
    )


def test_r7_only_cannbot_returns_override_intact(pb, monkeypatch):
    """无 self-built + 有 override → 返回 override 不变."""
    override = "## Available Skills (phase=design)\n\n- **a**: d\n"
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [])
    out = pb._build_skills_layer(override=override, task_type="develop")
    assert out == override


def test_r7_only_self_built_adds_marker(pb, monkeypatch):
    """无 override + 有 self-built → 加上 (self-built) 标题标记."""
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            _fake_skill("only-self", task_type="develop"),
        ],
    )
    out = pb._build_skills_layer(override=None, task_type="develop")
    assert "## Available Skills (self-built)" in out
    assert "- **only-self**:" in out


def test_r7_empty_returns_fallback_placeholder(pb, monkeypatch):
    """无 override 无 self-built → fallback 提示(PR-A 兼容)."""
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [])
    out = pb._build_skills_layer(override=None)
    assert "暂无自研 skill 沉淀" in out
    assert "/learn" in out


# ---- build_system_prompt 透传 recent_loads ---------------------------------


def test_build_system_prompt_accepts_recent_loads(monkeypatch):
    """build_system_prompt 加 recent_loads 参数并透传到 Layer 6 (R5b hit)."""
    p = PromptBuilder()

    # Mock MemoryStore & SOUL.md(避免文件依赖)
    class _MS:
        def format_for_system_prompt(self, section: str) -> str:
            return ""

    captured = {"recent_loads": None, "task_type": None}

    def _spy_layer(override, task_type=None, recent_loads=None):
        captured["recent_loads"] = recent_loads
        captured["task_type"] = task_type
        return ""

    monkeypatch.setattr(p, "_build_skills_layer", _spy_layer)
    p.build_system_prompt(
        workspace_path="/tmp",
        memory_store=_MS(),
        skills_layer_override=None,
        task_type="develop",
        recent_loads=["x", "y"],
    )
    assert captured["recent_loads"] == ["x", "y"]
    assert captured["task_type"] == "develop"
