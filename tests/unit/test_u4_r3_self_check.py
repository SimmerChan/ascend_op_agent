# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")
"""Tests for PR-B U4: R3 LLM self-check mechanism (agent/skill_standards.py +
dormant default + skill>20 trigger).

Covers:
  - SELF_CHECK_TRIGGER_THRESHOLD = 20 (plan Q2 决议, hermes-style default)
  - should_trigger_self_check: 三态决策(enabled / threshold hit / skill_count)
  - build_self_check_prompt: 占位 + 共用 single-source-of-truth (U4 plan)
  - render_system_prompt_with_self_check: dormant 默认不改 base_prompt
  - count_self_built_skills + list_self_built_skill_names: SkillsIndex → fallback SkillStorage

PhaseRunner integration (active path) 不在 U4 单测中显式测 —— 由 U5 整合测覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend_op_agent.agent.skill_standards import (
    SELF_CHECK_PROMPT_TEMPLATE,
    SELF_CHECK_TRIGGER_THRESHOLD,
    SkillSelfCheckConfig,
    build_self_check_prompt,
    count_self_built_skills,
    list_self_built_skill_names,
    render_system_prompt_with_self_check,
    should_trigger_self_check,
)


# ---- Threshold constant --------------------------------------------------


def test_threshold_constant_matches_plan():
    """SELF_CHECK_TRIGGER_THRESHOLD 必须 = 20 (plan Q2 决议 + hermes default)."""
    assert SELF_CHECK_TRIGGER_THRESHOLD == 20


# ---- should_trigger_self_check 三态决策 -----------------------------------


def test_trigger_disabled_returns_false():
    """cfg.enabled=False → 始终 False (dormant default)."""
    cfg = SkillSelfCheckConfig(enabled=False, threshold=20)
    assert should_trigger_self_check(cfg, skill_count=100) is False
    assert should_trigger_self_check(cfg, skill_count=21) is False


def test_trigger_below_threshold_returns_false():
    """enabled=True + skill_count ≤ threshold → False."""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    assert should_trigger_self_check(cfg, skill_count=0) is False
    assert should_trigger_self_check(cfg, skill_count=19) is False
    assert should_trigger_self_check(cfg, skill_count=20) is False  # boundary inclusive


def test_trigger_above_threshold_returns_true():
    """enabled=True + skill_count > threshold → True."""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    assert should_trigger_self_check(cfg, skill_count=21) is True
    assert should_trigger_self_check(cfg, skill_count=100) is True


def test_trigger_explicit_threshold_override():
    """threshold 可自定义(集成测试用,默认 20 但允许 override)."""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=5)
    assert should_trigger_self_check(cfg, skill_count=5) is False
    assert should_trigger_self_check(cfg, skill_count=6) is True


# ---- build_self_check_prompt 占位 + 共用 ------------------------------------


def test_build_self_check_prompt_contains_counts():
    """skill_count + threshold 占位正确."""
    out = build_self_check_prompt(skill_count=42, threshold=20)
    assert "42" in out
    assert "20" in out
    assert "Skill Self-Check" in out  # 标题


def test_build_self_check_prompt_text_stable():
    """占位 + 关键句稳定 —— 共用 single source of truth(plan: prompt 位置 = agent/skill_standards.py 共用)."""
    out = build_self_check_prompt(skill_count=25)
    # 关键中文句子必须在
    assert "被动 self-check" in out
    assert "不阻塞" in out
    # 三步式步骤
    assert "1." in out
    assert "2." in out
    assert "3." in out


def test_prompt_template_constant_matches_helper():
    """SELF_CHECK_PROMPT_TEMPLATE 与 build_self_check_prompt 输出占位一致."""
    expected = SELF_CHECK_PROMPT_TEMPLATE.format(skill_count=10, threshold=20)
    actual = build_self_check_prompt(skill_count=10, threshold=20)
    assert expected == actual


# ---- render_system_prompt_with_self_check 拼接策略 -----------------------


def test_render_dormant_unchanged():
    """cfg.enabled=False → 返回 base_prompt 原样(dormant by default)."""
    cfg = SkillSelfCheckConfig(enabled=False)
    base = "## Layer 6\nstuff\n"
    out = render_system_prompt_with_self_check(base, cfg)
    assert out == base


def test_render_below_threshold_unchanged():
    """enabled=True + skill_count ≤ threshold → 原样(monkeypatched计数)."""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    base = "## Base\n"
    out = render_system_prompt_with_self_check(base, cfg)
    # No self-built skills in test env → skill_count=0 → unchanged
    assert out == base


def test_render_above_threshold_appends_section(monkeypatch):
    """enabled=True + skill_count > threshold → 追加 self-check 段."""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    # 短路 count 自检函数
    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 25,
    )
    base = "## Layer 6\n- **tiling**: desc\n"
    out = render_system_prompt_with_self_check(base, cfg)
    assert "## Skill Self-Check" in out
    assert "25" in out
    assert "20" in out
    # base 仍在前面
    assert out.index("## Layer 6") < out.index("## Skill Self-Check")


# ---- list_self_built_skill_names + count_self_built_skills ------------------


def _write_skill_md(skills_dir: Path, name: str, *, task_type: str, topic: str) -> None:
    """Write a SKILL.md to <skills_dir>/self-built/{name}/ — frontmatter nested under ascend_op_agent."""
    skill_dir = skills_dir / "self-built" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": name,
        "description": f"desc-{name}",
        "tags": [],
        "metadata": {"ascend_op_agent": {"task_type": task_type, "topic": topic}},
    }
    body = f"---\n{json.dumps(fm)}\n---\n\n# {name}\n\n## Project Scope\nscope for {name}\n"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_list_self_built_returns_names(tmp_path, monkeypatch):
    """list_self_built_skill_names 返回 self-built/* 的子目录名."""
    from ascend_op_agent.skills.storage import SkillStorage

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill_md(skills_dir, "dev-tiling", task_type="develop", topic="tiling")
    _write_skill_md(skills_dir, "dev-runtime", task_type="develop", topic="runtime")
    _write_skill_md(skills_dir, "mig-cuda", task_type="migrate", topic="cuda")

    # 把 SkillStorage 默认路径指向 tmp(同 U2 fixture 模式)
    orig_init = SkillStorage.__init__

    def _init(self, skills_dir_arg=None):
        orig_init(self, skills_dir=str(skills_dir_arg or skills_dir))

    monkeypatch.setattr(SkillStorage, "__init__", _init, raising=True)

    names = list_self_built_skill_names()
    assert sorted(names) == ["dev-runtime", "dev-tiling", "mig-cuda"]


def test_count_self_built_no_task_type_uses_storage(monkeypatch, tmp_path):
    """count_self_built_skills(task_type=None) → SkillStorage 路径,返回总数."""
    from ascend_op_agent.skills.storage import SkillStorage

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for i in range(3):
        _write_skill_md(skills_dir, f"s{i}", task_type="develop", topic="t")

    orig_init = SkillStorage.__init__

    def _init(self, skills_dir_arg=None):
        orig_init(self, skills_dir=str(skills_dir_arg or skills_dir))

    monkeypatch.setattr(SkillStorage, "__init__", _init, raising=True)

    assert count_self_built_skills() == 3


def test_should_trigger_sk_uses_count_internally(monkeypatch):
    """skill_count=None → 内部调 count_self_built_skills 计算。"""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 25,
    )
    assert should_trigger_self_check(cfg) is True
    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 19,
    )
    assert should_trigger_self_check(cfg) is False
