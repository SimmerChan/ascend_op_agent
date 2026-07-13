# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")

"""Tests for skill_manage agent tool (PR-A U4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ascend_op_agent.agent.tools.skill_manage_tool import skill_manage


VALID_BODY = """## Project Scope

Applies to 910B3 + ops_pt + CANN 9.1.0 only.

## When to Use

Use when the user wants to debug CPU-Ascend divergence in tiling.
"""


def _use_tmp_storage(tmp_path, monkeypatch):
    """Route every ``SkillStorage()`` call inside the tool to ``tmp_path``.

    Tools construct ``SkillStorage()`` (no args) internally per action handler;
    we monkeypatch ``__init__`` so the real class binds ``skills_dir`` to our
    sandbox instead of ``~/.ascend_op_agent/skills``.
    """
    from ascend_op_agent.skills import storage as storage_mod
    _orig_init = storage_mod.SkillStorage.__init__

    def _init(self, skills_dir=None):
        _orig_init(self, skills_dir=str(tmp_path))

    monkeypatch.setattr(storage_mod.SkillStorage, "__init__", _init)


def test_create_happy_path_writes_to_self_built(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    result = skill_manage(
        action="create",
        name="tiling-pitfalls",
        description="Common Ascend tiling pitfalls",
        task_type="develop",
        topic="tiling",
        body=VALID_BODY,
    )
    assert result["success"] is True
    assert result["data"]["name"] == "tiling-pitfalls"
    skill_path = tmp_path / "self-built" / "tiling-pitfalls" / "SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text(encoding="utf-8")
    assert "name: tiling-pitfalls" in content
    assert "task_type: develop" in content
    assert "topic: tiling" in content
    assert "write_origin: manual" in content  # provenance R4


def test_patch_full_replacement_requires_body(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    # First create
    skill_manage(
        action="create",
        name="tiling-pitfalls", description="x", task_type="develop",
        topic="tiling", body=VALID_BODY,
    )
    # Patch without body should be rejected (full-replacement semantics).
    res = skill_manage(
        action="patch",
        name="tiling-pitfalls", description="y", task_type="develop",
        topic="tiling", body="",  # empty
    )
    assert res["success"] is False
    assert res["error"] == "validation_failed"
    assert res["field"] == "body"


def test_patch_overwrites_existing(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create",
        name="tiling-pitfalls", description="x", task_type="develop",
        topic="tiling", body=VALID_BODY,
    )
    new_body = "## Project Scope\n\nRevised.\n"
    res = skill_manage(
        action="patch",
        name="tiling-pitfalls", description="y", task_type="develop",
        topic="tiling", body=new_body,
    )
    assert res["success"] is True
    skill_path = tmp_path / "self-built" / "tiling-pitfalls" / "SKILL.md"
    assert "Revised." in skill_path.read_text(encoding="utf-8")


def test_archive_moves_to_dot_archived_subdir(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create",
        name="tiling-pitfalls", description="x", task_type="develop",
        topic="tiling", body=VALID_BODY,
    )
    res = skill_manage(
        action="archive",
        skill_name="tiling-pitfalls",
        archive_reason="superseded by v2",
    )
    assert res["success"] is True
    skill_root = tmp_path / "self-built" / "tiling-pitfalls"
    archived = skill_root / ".archived" / "SKILL.md"
    assert archived.exists()
    content = archived.read_text(encoding="utf-8")
    assert "archive_at:" in content or "archive_at" in content
    assert "superseded by v2" in content


def test_load_returns_skill_metadata(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create",
        name="tiling-pitfalls", description="debug tiling",
        task_type="develop", topic="tiling", body=VALID_BODY,
    )
    res = skill_manage(action="load", skill_name="tiling-pitfalls")
    assert res["success"] is True
    assert res["data"]["name"] == "tiling-pitfalls"
    assert res["data"]["description"] == "debug tiling"


def test_list_and_search_use_frontmatter_scan(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    # Two skills with different task_types
    skill_manage(
        action="create", name="tiling-pitfalls",
        description="t1", task_type="develop", topic="tiling",
        body=VALID_BODY,
    )
    skill_manage(
        action="create", name="cann-runtime-debug",
        description="t2", task_type="migrate", topic="runtime",
        body=VALID_BODY,
    )
    # list_skills returns both
    listed = skill_manage(action="list_skills")["data"]
    names = sorted(s["name"] for s in listed)
    assert names == ["cann-runtime-debug", "tiling-pitfalls"]

    # search by task_type=develop filters correctly (no FTS5 — frontmatter scan)
    only_dev = skill_manage(
        action="search", search_task_type="develop"
    )["data"]
    assert [s["name"] for s in only_dev] == ["tiling-pitfalls"]

    only_mig = skill_manage(
        action="search", search_task_type="migrate"
    )["data"]
    assert [s["name"] for s in only_mig] == ["cann-runtime-debug"]


def test_search_with_topic_filter(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create", name="alpha", description="x",
        task_type="develop", topic="tiling", body=VALID_BODY,
    )
    skill_manage(
        action="create", name="beta", description="x",
        task_type="develop", topic="runtime", body=VALID_BODY,
    )
    res = skill_manage(
        action="search", search_task_type="develop", search_topic="runtime"
    )["data"]
    assert [s["name"] for s in res] == ["beta"]


def test_create_rejects_invalid_name_regex(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    # Names with PR-number tokens are rejected.
    res = skill_manage(
        action="create", name="pr-123-fix",
        description="x", task_type="develop", topic="tiling",
        body=VALID_BODY,
    )
    assert res["success"] is False
    assert res["error"] == "validation_failed"
    assert res["field"] == "name"


def test_create_rejects_invalid_task_type(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    res = skill_manage(
        action="create", name="valid-name",
        description="x", task_type="NOT_A_TYPE", topic="tiling",
        body=VALID_BODY,
    )
    assert res["success"] is False
    assert res["field"] == "task_type"


def test_create_rejects_body_without_project_scope(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    bad_body = "## Overview\n\nNo project scope here.\n"
    res = skill_manage(
        action="create", name="valid-name",
        description="x", task_type="develop", topic="tiling",
        body=bad_body,
    )
    assert res["success"] is False
    assert res["field"] == "body"
    assert "Project Scope" in res["reason"]


def test_description_long_soft_warns_but_does_not_block(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    long_desc = "x" * 80  # > 40 char KTD-2 limit
    res = skill_manage(
        action="create", name="tiling-x",
        description=long_desc, task_type="develop", topic="tiling",
        body=VALID_BODY,
    )
    assert res["success"] is True
    assert "warning" in res  # soft-warn key per R16


def test_unknown_action_returns_validation_error(tmp_path, monkeypatch):
    _use_tmp_storage(tmp_path, monkeypatch)
    res = skill_manage(action="nope")
    assert res["success"] is False
    assert res["error"] == "unknown_action"


def test_load_skill_after_create_finds_self_built(tmp_path, monkeypatch):
    """F4 fix verification: load_skill resolves bare name through self-built/ subdir."""
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create", name="tiling-pitfalls",
        description="x", task_type="develop", topic="tiling",
        body=VALID_BODY,
    )
    # After save to self-built/tiling_pitfalls/, list_skills + load round-trip
    listed = skill_manage(action="list_skills")["data"]
    assert any(s["name"] == "tiling-pitfalls" for s in listed)
    loaded = skill_manage(action="load", skill_name="tiling-pitfalls")
    assert loaded["success"] is True
    assert loaded["data"]["description"] == "x"


def test_add_reference_writes_reference_skill(tmp_path, monkeypatch):
    """R13: add_reference writes {skill_name}_{reference_name}_reference/."""
    _use_tmp_storage(tmp_path, monkeypatch)
    skill_manage(
        action="create", name="tiling-pitfalls",
        description="x", task_type="develop", topic="tiling",
        body=VALID_BODY,
    )
    ref_file = tmp_path / "ref.txt"
    ref_file.write_text("content of reference\n", encoding="utf-8")
    res = skill_manage(
        action="add_reference",
        skill_name="tiling-pitfalls",
        reference_name="cmake_docs",
        reference_path=str(ref_file),
    )
    assert res["success"] is True
    ref_dir = tmp_path / "tiling-pitfalls_reference"
    assert (ref_dir / "SKILL.md").exists()
    content = (ref_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "content of reference" in content
