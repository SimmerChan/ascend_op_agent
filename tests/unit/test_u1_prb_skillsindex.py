# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")

"""Tests for PR-B U1: SkillsIndex FTS5 schema extension + crash-safe migration + CANBOT_BUNDLE_MAP."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ascend_op_agent.orchestrator.cannbot_loader import (
    CANBOT_BUNDLE_MAP,
    SKILL_BUNDLES,
)
from ascend_op_agent.orchestrator import CANBOT_BUNDLE_MAP as _EXPORTED_BUNDLE_MAP
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.models import Skill


def _make_index(tmp_path: Path) -> SkillIndex:
    """Construct SkillIndex with tmp db_path + cache_dir."""
    return SkillIndex(
        db_path=str(tmp_path / "skills_index.db"),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )


# ---- FTS5 schema v2 (6 列 + schema_meta) ----


def test_fresh_db_creates_6_columns_and_schema_meta(tmp_path):
    """Fresh DB → skills 表含 task_type + topic 两列 + schema_meta 表含 schema_version='2'."""
    _make_index(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "skills_index.db"))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(skills)")
    cols = [r[1] for r in cur.fetchall()]
    assert "task_type" in cols
    assert "topic" in cols
    cur.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    )
    assert cur.fetchone()[0] == "2"
    conn.close()


def test_add_skill_with_task_type_topic_stored_in_columns(tmp_path):
    """add_skill 新增 task_type/topic keyword-only 参数,写入 FTS5 列."""
    idx = _make_index(tmp_path)
    idx.add_skill(
        Skill(name="tiling-pitfalls", description="d", content="c",
              tags=["t1"]),
        task_type="develop",
        topic="tiling",
    )
    conn = sqlite3.connect(str(tmp_path / "skills_index.db"))
    cur = conn.cursor()
    cur.execute("SELECT name, task_type, topic FROM skills")
    rows = cur.fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0] == ("tiling-pitfalls", "develop", "tiling")


def test_add_skill_without_task_type_topic_uses_empty_string(tmp_path):
    """add_skill 不传 task_type/topic → FTS5 存 ''(兼容 PR-A 旧调用)."""
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="legacy", description="d", content="c"))
    conn = sqlite3.connect(str(tmp_path / "skills_index.db"))
    cur = conn.cursor()
    cur.execute("SELECT task_type, topic FROM skills WHERE name = 'legacy'")
    assert cur.fetchone() == ("", "")
    conn.close()


# ---- Crash-safe migration (PR-A round-2 P1 fix) ----


def test_migration_v1_to_v2_with_persist_backup(tmp_path):
    """模拟 v1 老 DB(4 列 + 无 schema_meta)→ migration 触发 → 备份存在 → 新 schema 生效 + 内容保留."""
    db_path = tmp_path / "skills_index.db"
    # 手工创建 v1 schema(4 列,无 schema_meta)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE VIRTUAL TABLE skills USING fts5(
            name, description, tags, content,
            tokenize='porter unicode61'
        )
    """)
    cur.execute(
        "INSERT INTO skills (name, description, tags, content) "
        "VALUES ('old-skill', 'old-desc', 'a,b', 'old-content')"
    )
    conn.commit()
    conn.close()

    # 触发 migration
    _make_index(tmp_path)

    # v2 schema 生效
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(skills)")
    cols = [r[1] for r in cur.fetchall()]
    assert "task_type" in cols and "topic" in cols
    # 内容保留
    cur.execute("SELECT name, description, content FROM skills")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "old-skill"
    assert rows[0][1] == "old-desc"
    assert rows[0][2] == "old-content"
    # task_type/topic 暂 NULL(迁移期)
    cur.execute("SELECT task_type, topic FROM skills WHERE name = 'old-skill'")
    assert cur.fetchone() == ("", "")
    # schema_version 写入
    cur.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
    assert cur.fetchone()[0] == "2"
    conn.close()
    # 备份被清理
    backup = tmp_path / ".skills_index.bak.json"
    assert not backup.exists()


def test_migration_crash_restore_from_disk_backup(tmp_path):
    """模拟 crash: 备份存在 + schema_version 未升 → 启动时自动 restore 内容."""
    db_path = tmp_path / "skills_index.db"
    # 手工创建 v1 + 写 crash-state backup
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE VIRTUAL TABLE skills USING fts5(
            name, description, tags, content, task_type, topic,
            tokenize='porter unicode61'
        )
    """)
    cur.execute("""
        CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)
    """)
    # 不写 schema_version='2',模拟崩溃
    conn.commit()
    conn.close()

    # 写 disk backup 模拟 migrate 写到一半
    backup_path = tmp_path / ".skills_index.bak.json"
    backup_data = {
        "migrating": True,
        "timestamp": 0,
        "rows": [
            {
                "name": "restored-skill",
                "description": "restored-desc",
                "tags": "x",
                "content": "restored-content",
                "task_type": "develop",
                "topic": "tiling",
            }
        ],
    }
    backup_path.write_text(json.dumps(backup_data), encoding="utf-8")

    # 触发 init
    _make_index(tmp_path)

    # Restore 生效
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name, description, task_type, topic FROM skills")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("restored-skill", "restored-desc", "develop", "tiling")
    cur.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
    assert cur.fetchone()[0] == "2"
    conn.close()
    # 备份被清理
    assert not backup_path.exists()


def test_migration_idempotent_on_already_v2(tmp_path):
    """v2 DB 再启动 → schema_version 已为 '2' → migration 跳过."""
    idx = _make_index(tmp_path)  # 创建 v2
    # 写一个 skill
    idx.add_skill(Skill(name="x", description="d", content="c"))
    # 再构造一次 _make_index(应 skip migration,内容仍在)
    idx2 = _make_index(tmp_path)
    conn = sqlite3.connect(str(tmp_path / "skills_index.db"))
    cur = conn.cursor()
    cur.execute("SELECT name FROM skills")
    assert cur.fetchall() == [("x",)]
    conn.close()


# ---- _do_search with task_type/topic filter ----


def test_search_filters_by_task_type(tmp_path):
    """search(query, k, task_type=...) 只返回 task_type 匹配的 skill."""
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="dev-a", description="alpha skill", content="c"),
                  task_type="develop", topic="tiling")
    idx.add_skill(Skill(name="dev-b", description="beta skill", content="c"),
                  task_type="develop", topic="runtime")
    idx.add_skill(Skill(name="mig-c", description="gamma skill", content="c"),
                  task_type="migrate", topic="cuda_frontend")

    # query "skill" 命中所有 desc,task_type 过滤后只留 matching
    only_dev = idx.hybrid_search(query="skill", k=10, task_type="develop")
    assert sorted(s.name for s in only_dev) == ["dev-a", "dev-b"]
    only_mig = idx.hybrid_search(query="skill", k=10, task_type="migrate")
    assert [s.name for s in only_mig] == ["mig-c"]


def test_search_filters_by_topic(tmp_path):
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="a", description="a-skill", content="c"),
                  task_type="develop", topic="tiling")
    idx.add_skill(Skill(name="b", description="b-skill", content="c"),
                  task_type="develop", topic="runtime")

    only_tiling = idx.hybrid_search(query="skill", k=10, topic="tiling")
    assert [s.name for s in only_tiling] == ["a"]
    only_runtime = idx.hybrid_search(query="skill", k=10, topic="runtime")
    assert [s.name for s in only_runtime] == ["b"]


def test_search_combined_task_type_and_topic_AND(tmp_path):
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="dev-tiling", description="skill", content="c"),
                  task_type="develop", topic="tiling")
    idx.add_skill(Skill(name="dev-runtime", description="skill", content="c"),
                  task_type="develop", topic="runtime")
    idx.add_skill(Skill(name="mig-tiling", description="skill", content="c"),
                  task_type="migrate", topic="tiling")

    res = idx.hybrid_search(query="skill", k=10,
                            task_type="develop", topic="tiling")
    assert [s.name for s in res] == ["dev-tiling"]


def test_search_no_filter_returns_all_matching(tmp_path):
    """不传 task_type/topic → 不过滤,返回所有 FTS5 匹配."""
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="dev", description="x", content="c"),
                  task_type="develop", topic="tiling")
    idx.add_skill(Skill(name="mig", description="x", content="c"),
                  task_type="migrate", topic="cuda")
    res = idx.hybrid_search(query="x", k=10)
    assert sorted(s.name for s in res) == ["dev", "mig"]


# ---- search_by_task_type helper ----


def test_search_by_task_type_returns_only_matching(tmp_path):
    idx = _make_index(tmp_path)
    idx.add_skill(Skill(name="a", description="x", content="c"),
                  task_type="develop", topic="t1")
    idx.add_skill(Skill(name="b", description="x", content="c"),
                  task_type="develop", topic="t2")
    idx.add_skill(Skill(name="c", description="x", content="c"),
                  task_type="migrate", topic="t3")
    res = idx.search_by_task_type("develop")
    assert sorted(s.name for s in res) == ["a", "b"]


# ---- CANBOT_BUNDLE_MAP ----


def test_canbot_bundle_map_covers_all_skill_bundles():
    """CANBOT_BUNDLE_MAP 必须覆盖 SKILL_BUNDLES 的所有 7 (graph, phase) 键."""
    sb_keys = set(SKILL_BUNDLES.keys())
    cbm_keys = set(CANBOT_BUNDLE_MAP.keys())
    assert sb_keys == cbm_keys, (
        f"missing in CANBOT_BUNDLE_MAP: {sb_keys - cbm_keys}; "
        f"extra: {cbm_keys - sb_keys}"
    )


def test_canbot_bundle_map_targets_valid_task_types():
    """CANBOT_BUNDLE_MAP 的 value (task_type, topic) 必为 TASK_TYPES 已知值."""
    valid_tt = {"migrate", "analyze", "optimize", "develop"}
    for graph_phase, (tt, topic) in CANBOT_BUNDLE_MAP.items():
        assert tt in valid_tt, f"{graph_phase} → unknown task_type {tt!r}"


def test_canbot_bundle_map_exported_from_orchestrator():
    """CANBOT_BUNDLE_MAP 必须在 orchestractor.__init__ 导出(R5b 路由要 import)."""
    assert _EXPORTED_BUNDLE_MAP is CANBOT_BUNDLE_MAP
