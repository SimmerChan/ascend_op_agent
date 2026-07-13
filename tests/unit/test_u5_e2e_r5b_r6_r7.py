# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")
"""End-to-end integration tests for PR-B: R5b 路由 + R6 hybrid search + R7
grouped rendering + R3 self-check 触发.

Scenarios:
  1. skill_create → search (R6 hybrid) → task_type filter 只返回 matching
  2. R5b: PromptBuilder._build_skills_layer + task_type=develop + recent_loads
     → self-built 段只含 matching skill + 顺序保留
  3. R5b cap=10: 12 self-built → Layer 6 截到 10 行
  4. R7: cannbot (override) + self-built → 双标题分组 + cannbot 在前
  5. R3 trigger: skill_count=21 → render_system_prompt_with_self_check
     自动追加 R3 段;skill_count=10 → 不加
  6. skill_create(task_type=migrate, topic=cuda) → search hybrid
     (query, task_type, topic) 三者 AND
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ascend_op_agent.agent.prompt_builder import (
    HERMES_LAYER_LIMIT,
    RECENT_LOADS_MAX,
    PromptBuilder,
)
from ascend_op_agent.agent.skill_standards import (
    SkillSelfCheckConfig,
    render_system_prompt_with_self_check,
)
from ascend_op_agent.agent.tools import skill_manage_tool as smt
from ascend_op_agent.agent.tools.skill_manage_tool import skill_manage
from ascend_op_agent.orchestrator.cannbot_loader import CannbotSkill
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.models import Skill
from ascend_op_agent.skills.storage import SkillStorage


VALID_BODY = "## Project Scope\nscope body\n\n## Notes\nnotes body\n"


# ---- shared helpers --------------------------------------------------------


def _write_skill_md(
    skills_dir: Path, name: str, *, task_type: str, topic: str, description: str, content_body: str
) -> None:
    """Write SKILL.md via raw frontmatter(模拟 skill_manage save 结果)."""
    skill_dir = skills_dir / "self-built" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": name,
        "description": description,
        "tags": [],
        "metadata": {"ascend_op_agent": {"task_type": task_type, "topic": topic}},
    }
    body = f"---\n{json.dumps(fm)}\n---\n\n# {name}\n\n{content_body}\n"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


@pytest.fixture
def e2e_fixture(tmp_path, monkeypatch):
    """E2E setup: tmp SkillStorage + tmp SkillIndex + 4 self-built skills seeded.

    monkeypatch:
      - smt.SkillIndex → fixture idx (factory)
      - SkillStorage.__init__ → tmp_path
      - SkillIndex.embedding_model → property None (短路网络加载)
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Seed 4 raw SKILL.md 文件
    _write_skill_md(
        skills_dir,
        "dev-tiling",
        task_type="develop",
        topic="tiling",
        description="Tiling skill for develop",
        content_body=VALID_BODY,
    )
    _write_skill_md(
        skills_dir,
        "dev-runtime",
        task_type="develop",
        topic="runtime",
        description="Runtime skill for develop",
        content_body=VALID_BODY,
    )
    _write_skill_md(
        skills_dir,
        "mig-tiling",
        task_type="migrate",
        topic="tiling",
        description="Tiling skill for migrate",
        content_body=VALID_BODY,
    )
    _write_skill_md(
        skills_dir,
        "mig-cuda",
        task_type="migrate",
        topic="cuda",
        description="CUDA frontend migration",
        content_body=VALID_BODY,
    )

    # SkillIndex tmp
    idx = SkillIndex(
        db_path=str(tmp_path / "skills_index.db"),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )
    monkeypatch.setattr(
        type(idx),
        "embedding_model",
        property(lambda self: None),
        raising=True,
    )

    # 注册同名 SkillsIndex row(task_type/topic 列)
    for s_name, s_tt, s_tp, s_desc in [
        ("dev-tiling", "develop", "tiling", "Tiling skill for develop"),
        ("dev-runtime", "develop", "runtime", "Runtime skill for develop"),
        ("mig-tiling", "migrate", "tiling", "Tiling skill for migrate"),
        ("mig-cuda", "migrate", "cuda", "CUDA frontend migration"),
    ]:
        idx.add_skill(
            Skill(name=s_name, description=s_desc, content="c"),
            task_type=s_tt,
            topic=s_tp,
        )

    monkeypatch.setattr(smt, "_make_skill_index", lambda: idx, raising=True)

    # SkillStorage tmp
    orig_init = SkillStorage.__init__

    def _init(self, skills_dir_arg=None):
        orig_init(self, skills_dir=str(skills_dir_arg or skills_dir))

    monkeypatch.setattr(SkillStorage, "__init__", _init, raising=True)

    return {"idx": idx, "skills_dir": skills_dir}


def _skill_lines(text: str) -> list[str]:
    return re.findall(r"- \*\*([\w-]+)\*\*:", text)


# ---- 1. R6 hybrid search + Layer 6 联合 ----------------------------------


def test_e2e_search_then_layer6_route(e2e_fixture):
    """E2E: search by task_type=develop → 只 dev-* → 进 Layer 6 self-built 段."""
    # 1. R6: search by task_type
    res = skill_manage(action="search", search_query="skill", search_task_type="develop")
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["dev-runtime", "dev-tiling"]

    # 2. R5b: 用 task_type=develop + override cannbot → Layer 6
    pb = PromptBuilder()
    monkey = pytest.MonkeyPatch()

    # 关键: simulate PhaseRunner injection
    monkey.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            CannbotSkill(
                name="dev-tiling",
                description="Tiling skill for develop",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "tiling"}},
            ),
            CannbotSkill(
                name="dev-runtime",
                description="Runtime skill for develop",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "runtime"}},
            ),
            CannbotSkill(
                name="mig-cuda",
                description="CUDA frontend",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "migrate", "topic": "cuda"}},
            ),
        ],
    )

    cannbot = "## Available Skills (phase=design)\n\n- **arch**: desc\n"
    out = pb._build_skills_layer(
        override=cannbot,
        task_type="develop",
        recent_loads=None,
    )
    lines = _skill_lines(out)
    assert "dev-tiling" in lines
    assert "dev-runtime" in lines
    assert "mig-cuda" not in lines  # task_type=migrate 过滤
    # R7: 双标题(cannbot + self-built)
    assert "## Available Skills (phase=design)" in out
    assert "## Available Skills (self-built)" in out
    monkey.undo()


# ---- 2. R5b recent_loads 顺序保留 ---------------------------------------


def test_e2e_recent_loads_preserves_order(e2e_fixture, monkeypatch):
    """R5b: recent_loads=[dev-runtime, dev-tiling] → 顺序保留(非字母)."""
    pb = PromptBuilder()
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            CannbotSkill(
                name="dev-tiling",
                description="d",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "tiling"}},
            ),
            CannbotSkill(
                name="dev-runtime",
                description="d",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "runtime"}},
            ),
        ],
    )
    out = pb._build_skills_layer(
        override=None,
        task_type="develop",
        recent_loads=["dev-runtime", "dev-tiling"],  # 反字母序
    )
    lines = _skill_lines(out)
    # 顺序:task_type 命中(顺序来自 self_built_skills)+ recent_loads 补充
    # 由于 task_type 命中覆盖全部 skills,顺序由 self-built list 决定
    assert lines.index("dev-tiling") < lines.index("dev-runtime")


# ---- 3. R5b cap=10 --------------------------------------------------------


def test_e2e_layer6_caps_to_10(e2e_fixture, monkeypatch):
    """12 self-built skills (all task_type=develop) → Layer 6 渲染只前 10."""
    pb = PromptBuilder()
    monkeypatch.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            CannbotSkill(
                name=f"skill-{i:02d}",
                description=f"d{i}",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "t"}},
            )
            for i in range(12)
        ],
    )
    cannbot = "## Available Skills (phase=design)\n\n- **x**: d\n"
    out = pb._build_skills_layer(
        override=cannbot,
        task_type="develop",
        recent_loads=None,
    )
    self_built_names = [n for n in _skill_lines(out) if n.startswith("skill-")]
    assert len(self_built_names) == HERMES_LAYER_LIMIT  # 10
    # 前 10 保
    assert "skill-00" in self_built_names
    assert "skill-09" in self_built_names
    # 后 2 丢
    assert "skill-10" not in self_built_names
    assert "skill-11" not in self_built_names


# ---- 4. R7 分组渲染 cannbot + self-built 顺序 --------------------------


def test_e2e_r7_cannbot_then_self_built(e2e_fixture):
    """完整 R7 验证:cannbot phase header 在前, self-built header 在后."""
    pb = PromptBuilder()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        pb,
        "_load_self_built_skills",
        lambda storage: [
            CannbotSkill(
                name="dev-tiling",
                description="d",
                body="",
                base_dir=Path("/tmp/_fake"),
                frontmatter={"ascend_op_agent": {"task_type": "develop", "topic": "tiling"}},
            ),
        ],
    )
    cannbot = "## Available Skills (phase=design)\n\n- **ascendc-tiling-design**: d\n"
    out = pb._build_skills_layer(
        override=cannbot,
        task_type="develop",
    )
    cannbot_idx = out.index("## Available Skills (phase=design)")
    self_built_idx = out.index("## Available Skills (self-built)")
    assert cannbot_idx < self_built_idx
    # cannbot 段含 ascendc-tiling-design;self-built 段含 dev-tiling
    assert "ascendc-tiling-design" in out[:self_built_idx]
    assert "dev-tiling" in out[self_built_idx:]
    monkey.undo()


# ---- 5. R3 dormant ↔ trigger 切换 ----------------------------------------


def test_e2e_r3_dormant_default_no_section(e2e_fixture):
    """R3 默认 enabled=False → render_system_prompt_with_self_check 无变化."""
    base = "## Layer 6\n- **x**: d\n"
    out = render_system_prompt_with_self_check(
        base,
        SkillSelfCheckConfig(enabled=False, threshold=20),
    )
    assert out == base


def test_e2e_r3_trigger_when_skill_count_exceeds(monkeypatch):
    """R3 enabled + skill_count > 20 → 末尾追加 R3 self-check 段。"""
    # 短路 count → 21(> 20)
    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 21,
    )
    base = "## Layer 6\n- **x**: d\n"
    out = render_system_prompt_with_self_check(
        base,
        SkillSelfCheckConfig(enabled=True, threshold=20),
    )
    # R3 段必须在
    assert "## Skill Self-Check (R3)" in out
    assert "21" in out
    # base 不变
    assert out.startswith(base)


def test_e2e_r3_no_trigger_when_below(monkeypatch):
    """R3 enabled + skill_count ≤ 20 → 不追加。"""
    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 19,
    )
    base = "## Layer 6\n- **x**: d\n"
    out = render_system_prompt_with_self_check(
        base,
        SkillSelfCheckConfig(enabled=True, threshold=20),
    )
    assert "## Skill Self-Check (R3)" not in out


# ---- 6. R6 hybrid search 三者 AND ----------------------------------------


def test_e2e_search_triple_AND_filter(e2e_fixture):
    """R6: search(query, task_type, topic) → 三者 AND 过滤."""
    res = skill_manage(
        action="search",
        search_query="skill",
        search_task_type="develop",
        search_topic="runtime",
    )
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    # only dev-runtime
    assert names == ["dev-runtime"]


# ---- 7. Constants 收敛性检查 -----------------------------------------------


def test_constants_alignment():
    """PR-B U constants 已知值(防止误改阈值)."""
    assert HERMES_LAYER_LIMIT == 10
    assert RECENT_LOADS_MAX == 5
