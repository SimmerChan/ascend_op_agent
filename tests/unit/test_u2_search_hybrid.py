# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Tests for PR-B U2: skill_manage search action → hybrid (FTS5 + ChromaDB)
+ task_type/topic filter + 空 query 路由 + SkillsIndex 不可用 fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ascend_op_agent.agent.tools import skill_manage_tool as smt
from ascend_op_agent.agent.tools.skill_manage_tool import (
    SearchSkillsArgs,
    _action_search,
    skill_manage,
)
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.models import Skill
from ascend_op_agent.skills.storage import SkillStorage


# ---- helpers --------------------------------------------------------------


def _write_skill_md(skills_dir: Path, name: str, *, task_type: str, topic: str,
                    description: str, content_body: str) -> None:
    """Write a SKILL.md to <skills_dir>/self-built/{name}/SKILL.md."""
    skill_dir = skills_dir / "self-built" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": name,
        "description": description,
        "tags": [],
        "metadata": {
            "ascend_op_agent": {
                "task_type": task_type,
                "topic": topic,
            },
        },
    }
    body = f"---\n{json.dumps(fm)}\n---\n\n# {name}\n\n{content_body}\n"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    """临时 skills_dir + SkillIndex + 4 个 self-built skill(dev×2, mig×2).

    monkeypatch:
      - skill_manage_tool.SkillIndex   → 临时 fixture idx
      - SkillStorage.__init__          → 默认路径走 tmp_skills_dir
    """
    tmp_skills_dir = tmp_path / "skills"
    tmp_skills_dir.mkdir()

    _write_skill_md(
        tmp_skills_dir, "dev-tiling",
        task_type="develop", topic="tiling",
        description="Tiling skill for develop tasks",
        content_body="Use clean tiling API, avoid bank conflict.",
    )
    _write_skill_md(
        tmp_skills_dir, "dev-runtime",
        task_type="develop", topic="runtime",
        description="Runtime skill for develop tasks",
        content_body="Handle dynamic-shape w/o recompiling.",
    )
    _write_skill_md(
        tmp_skills_dir, "mig-tiling",
        task_type="migrate", topic="tiling",
        description="Tiling skill for migrate tasks",
        content_body="cuda frontend tiling mapping guidelines.",
    )
    _write_skill_md(
        tmp_skills_dir, "mig-cuda",
        task_type="migrate", topic="cuda",
        description="CUDA frontend migration skill",
        content_body="cuda frontend pitfalls and adaptions.",
    )

    idx = SkillIndex(
        db_path=str(tmp_path / "skills_index.db"),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )
    # 短路 sentence-transformers 网络加载(测试不需要 embedding,纯 FTS5 足够)
    monkeypatch.setattr(
        type(idx), "embedding_model",
        property(lambda self: None), raising=True,
    )
    # 注册到 FTS5(task_type/topic columns)
    idx.add_skill(Skill(name="dev-tiling", description="Tiling skill for develop tasks",
                        content="Use clean tiling API avoid bank conflict"),
                  task_type="develop", topic="tiling")
    idx.add_skill(Skill(name="dev-runtime", description="Runtime skill for develop tasks",
                        content="Handle dynamic-shape w/o recompiling"),
                  task_type="develop", topic="runtime")
    idx.add_skill(Skill(name="mig-tiling", description="Tiling skill for migrate tasks",
                        content="cuda frontend tiling mapping guidelines"),
                  task_type="migrate", topic="tiling")
    idx.add_skill(Skill(name="mig-cuda", description="CUDA frontend migration skill",
                        content="cuda frontend pitfalls and adaptions"),
                  task_type="migrate", topic="cuda")

    # SkillIndex factory: skill_manage_tool._make_skill_index → 我们的 idx
    monkeypatch.setattr(smt, "_make_skill_index", lambda: idx, raising=True)

    # SkillStorage: monkeypatch __init__ 让默认路径走 tmp_skills_dir
    orig_init = SkillStorage.__init__

    def _init(self, skills_dir=None):
        orig_init(self, skills_dir=str(skills_dir or tmp_skills_dir))

    monkeypatch.setattr(SkillStorage, "__init__", _init, raising=True)

    return {"idx": idx, "skills_dir": tmp_skills_dir}


# ---- happy path: hybrid search with filter ---------------------------------


def test_search_query_filters_by_task_type(fixture):
    """query="skill" + task_type="develop" → 只 dev-*(dev-tiling, dev-runtime)."""
    res = _action_search(
        SearchSkillsArgs(query="skill", task_type="develop")
    )
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["dev-runtime", "dev-tiling"]


def test_search_query_filters_by_topic(fixture):
    """query="skill" + topic="tiling" → 只 *-tiling(dev-tiling, mig-tiling)."""
    res = _action_search(
        SearchSkillsArgs(query="skill", topic="tiling")
    )
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["dev-tiling", "mig-tiling"]


def test_search_query_combined_task_type_and_topic(fixture):
    """query + task_type + topic → 三者 AND 过滤."""
    res = _action_search(
        SearchSkillsArgs(query="skill", task_type="develop", topic="tiling")
    )
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["dev-tiling"]


def test_search_query_no_filter_returns_all(fixture):
    """query 无 filter → 返回 4 个."""
    res = _action_search(SearchSkillsArgs(query="skill"))
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["dev-runtime", "dev-tiling", "mig-cuda", "mig-tiling"]


# ---- empty-query routing ---------------------------------------------------


def test_search_empty_query_with_task_type(fixture):
    """空 query + task_type → search_by_task_type 路径."""
    res = _action_search(
        SearchSkillsArgs(query=None, task_type="migrate")
    )
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["mig-cuda", "mig-tiling"]


def test_search_empty_query_task_type_plus_topic(fixture):
    """空 query + task_type + topic → search_by_task_type + topic 二级过滤."""
    res = _action_search(
        SearchSkillsArgs(query=None, task_type="develop", topic="runtime")
    )
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["dev-runtime"]


def test_search_empty_query_topic_only_goes_frontmatter(fixture):
    """空 query + 仅 topic → frontmatter fallback(Skill 对象不含 topic 列)."""
    res = _action_search(
        SearchSkillsArgs(query=None, topic="cuda")
    )
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["mig-cuda"]


def test_search_empty_query_no_filter_returns_empty(fixture):
    """空 query + 无 filter → frontmatter fallback 返回全部."""
    res = _action_search(SearchSkillsArgs(query=None))
    assert res["success"] is True
    names = sorted(d["name"] for d in res["data"])
    assert names == ["dev-runtime", "dev-tiling", "mig-cuda", "mig-tiling"]


# ---- SkillsIndex unavailable → frontmatter fallback -----------------------


def test_search_falls_back_when_index_unavailable(fixture, monkeypatch):
    """SkillIndex() instantiation raise → 走 frontmatter fallback."""
    def _fail():
        raise RuntimeError("embedding model missing")
    monkeypatch.setattr(smt, "_make_skill_index", _fail, raising=True)

    res = _action_search(
        SearchSkillsArgs(
            query=None,
            task_type="develop",
            topic="tiling",
        )
    )
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    # fallback 路径同时过滤 task_type=develop + topic=tiling → dev-tiling
    assert names == ["dev-tiling"]


def test_search_fallback_text_query_substring(fixture, monkeypatch):
    """fallback 路径下 query 做 name+desc 文本子串过滤."""
    def _fail():
        raise RuntimeError("embedding model missing")
    monkeypatch.setattr(smt, "_make_skill_index", _fail, raising=True)

    res = _action_search(SearchSkillsArgs(query="CUDA"))  # case-insensitive
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["mig-cuda"]


# ---- result shape contract -------------------------------------------------


def test_search_result_shape_has_required_keys(fixture):
    """返回 dict 含 name/description/task_type/topic (4 keys 稳定)."""
    res = _action_search(
        SearchSkillsArgs(query="tiling", task_type="develop")
    )
    assert res["success"] is True
    assert len(res["data"]) >= 1
    for entry in res["data"]:
        assert set(entry.keys()) == {"name", "description", "task_type", "topic"}


# ---- skill_manage entry-point wiring ---------------------------------------


def test_skill_manage_search_entry_passes_query(fixture):
    """skill_manage(action="search", search_query=...) → SearchSkillsArgs.query 透传."""
    res = skill_manage(action="search", search_query="tiling",
                       search_task_type="develop")
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["dev-tiling"]


def test_skill_manage_search_entry_passes_filters_only(fixture):
    """skill_manage(action="search", search_task_type=..., search_topic=...) 空 query."""
    res = skill_manage(action="search", search_task_type="migrate",
                       search_topic="cuda")
    assert res["success"] is True
    names = [d["name"] for d in res["data"]]
    assert names == ["mig-cuda"]


def test_skill_manage_search_entry_unknown_action(fixture):
    """skill_manage(action="unknown") → R16 structured error."""
    res = skill_manage(action="nonexistent")
    assert res["success"] is False
    assert res["error"] == "unknown_action"
