# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License")
"""PR-B U6 F-6: held-out adversarial test fixtures — 不公开在 U1/U3/U4 单测
中的越界/边界/对抗 cases,验证 PR-B 改动:

  - SkillsIndex migration 不 silent-corruption 在 hostile input 下
  - R5b HERMES_LAYER_LIMIT 边界 (exactly 10 vs 11)
  - R5b RECENT_LOADS_MAX 边界 (exactly 5 vs 6)
  - R3 dormant 在 task_type=None 时不踩 SkillsIndex(seek-double-free 风险)
  - skill_standards 共用 prompt 的 stable source-of-truth

filed under tests/unit/ to be picked up by ship_ready unit_test step.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ascend_op_agent.agent.prompt_builder import (
    HERMES_LAYER_LIMIT,
    RECENT_LOADS_MAX,
    PromptBuilder,
)
from ascend_op_agent.agent.skill_standards import (
    SELF_CHECK_PROMPT_TEMPLATE,
    build_self_check_prompt,
    count_self_built_skills,
    should_trigger_self_check,
    SkillSelfCheckConfig,
    render_system_prompt_with_self_check,
)
from ascend_op_agent.orchestrator.cannbot_loader import CannbotSkill
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.models import Skill
from ascend_op_agent.skills.storage import SkillStorage


# ---- F-6.1: migration adversarial ---------------------------------------


def test_f6_migration_with_unicode_content_in_v1(tmp_path):
    """v1 DB 含中文 + 特殊字符 → migration 不破 + 内容 1:1 保留。"""
    db_path = tmp_path / "skills_index.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE VIRTUAL TABLE skills USING fts5(
            name, description, tags, content,
            tokenize='porter unicode61'
        )
    """)
    rows = [
        ("ascii-skill", "desc ascii 中文 🚀", "tag1", "content-with-unicode"),
        ("long-desc", "x" * 5000, "t", "many words " * 200),
        ("newlines\nskill", "multi-line\ndescription", "t", "body\nwith\nnewlines"),
    ]
    cur.executemany(
        "INSERT INTO skills (name, description, tags, content) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    # 触发 migration
    SkillIndex(
        db_path=str(db_path),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )

    # 读回 v2 验证内容完全保留
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT name, description, content FROM skills ORDER BY name")
    out_rows = sorted(cur.fetchall())
    conn.close()
    assert out_rows[0][0] == "ascii-skill"
    assert "中文" in out_rows[0][1]
    assert "🚀" in out_rows[0][1]
    assert out_rows[1][0] == "long-desc"
    assert len(out_rows[1][1]) == 5000
    assert out_rows[2][0] == "newlines\nskill"


def test_f6_migration_idempotent_with_existing_task_type_topic(tmp_path):
    """v2 schema 已含 task_type/topic + schema_meta 缺 → migration 写 schema_meta 不删除 rows。"""
    db_path = tmp_path / "skills_index.db"
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
    cur.executemany(
        "INSERT INTO skills (name, description, tags, content, task_type, topic) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("a", "d", "t", "c", "develop", "tiling"),
            ("b", "d", "t", "c", "migrate", "cuda"),
        ],
    )
    conn.commit()
    conn.close()

    SkillIndex(
        db_path=str(db_path),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM skills")
    assert cur.fetchone()[0] == 2  # 防御:不能丢行
    cur.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
    assert cur.fetchone()[0] == "2"
    conn.close()


def test_f6_restore_with_corrupt_backup_does_not_crash(tmp_path):
    """backup 文件存在但 corrupt JSON → migration 优雅跳过,走正常路径。"""
    db_path = tmp_path / "skills_index.db"
    backup = tmp_path / ".skills_index.bak.json"
    # corrupt JSON
    backup.write_text("{not-valid-json", encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE VIRTUAL TABLE skills USING fts5(
            name, description, tags, content,
            tokenize='porter unicode61'
        )
    """)
    cur.execute("INSERT INTO skills (name, description, tags, content) VALUES ('x', 'd', 't', 'c')")
    conn.commit()
    conn.close()

    # 不抛
    SkillIndex(
        db_path=str(db_path),
        cache_dir=str(tmp_path),
        vector_store_dir=str(tmp_path / "vectors"),
    )

    # 备份仍然存在(因为 restore 失败时,后续 migration 流程正常完成)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM skills")
    assert cur.fetchone()[0] == 1  # 'x' 保留
    conn.close()


# ---- F-6.2: R5b 边界 -----------------------------------------------------


def _fake_skill(name: str, *, task_type: str = "") -> CannbotSkill:
    return CannbotSkill(
        name=name, description=f"d-{name}", body="",
        base_dir=Path("/tmp/_fake"),
        frontmatter={"ascend_op_agent": {"task_type": task_type, "topic": ""}},
    )


def test_f6_layer6_boundary_exactly_10(monkeypatch):
    """self-built = exactly HERMES_LAYER_LIMIT(10) → 全保留(不截)。"""
    pb = PromptBuilder()
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [
        _fake_skill(f"skill-{i:02d}", task_type="develop") for i in range(10)
    ])
    out = pb._build_skills_layer(override=None, task_type="develop")
    assert out.count("- **skill-") == 10
    assert "skill-09" in out


def test_f6_layer6_boundary_exactly_11(monkeypatch):
    """self-built = HERMES_LAYER_LIMIT+1(11) → 截到 10。"""
    pb = PromptBuilder()
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [
        _fake_skill(f"skill-{i:02d}", task_type="develop") for i in range(11)
    ])
    out = pb._build_skills_layer(override=None, task_type="develop")
    assert out.count("- **skill-") == 10
    assert "skill-10" not in out


def test_f6_layer6_recent_loads_boundary_exactly_5(monkeypatch):
    """recent_loads = exactly RECENT_LOADS_MAX(5) → 全保."""
    pb = PromptBuilder()
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [
        _fake_skill(f"r{i}", task_type="") for i in range(5)
    ])
    out = pb._build_skills_layer(
        override=None, recent_loads=[f"r{i}" for i in range(5)],
    )
    for i in range(5):
        assert f"r{i}" in out


def test_f6_layer6_recent_loads_boundary_exactly_6(monkeypatch):
    """recent_loads = RECENT_LOADS_MAX+1(6) → 只前 5 个(6 个 drop)。"""
    pb = PromptBuilder()
    monkeypatch.setattr(pb, "_load_self_built_skills", lambda storage: [
        _fake_skill(f"r{i}", task_type="") for i in range(6)
    ])
    out = pb._build_skills_layer(
        override=None, recent_loads=[f"r{i}" for i in range(6)],
    )
    # 前 5 全在
    for i in range(5):
        assert f"r{i}" in out
    # 第 6 不在
    assert "r5" not in out


# ---- F-6.3: R3 dormant / no SkillsIndex touch 边界 ----------------------


def test_f6_r3_count_with_no_task_type_does_not_touch_skills_index(monkeypatch):
    """count_self_built_skills(task_type=None) 不应该 lazy-load SkillsIndex。

    防御 lazy-import 不触发 sentence-transformers 加载(~56s 网络阻塞),也避免
    真实库搜索开销。dormant default 路径要 cheap。
    """
    sentinel = object()

    def _fail_if_called(*args, **kwargs):
        if kwargs.get("task_type") is not None:
            # task_type=None 路径不应触发
            raise AssertionError("count_self_built_skills hit SkillsIndex despite task_type=None")
        return 0

    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.list_self_built_skill_names",
        lambda skills_dir=None: [],
    )
    # SkillsIndex 类的实例化应该被绕开
    monkeypatch.setattr(
        "ascend_op_agent.skills.index.SkillIndex",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not instantiate SkillIndex")),
        raising=False,
    )
    # No error expected
    assert count_self_built_skills() == 0


def test_f6_r3_prompt_is_stable_source_of_truth():
    """prompt 共用 (plan: agent/skill_standards.py): build_* 与 TEMPLATE 一致。"""
    # 多次调用 output 严格 byte-identical
    a = build_self_check_prompt(skill_count=42, threshold=20)
    b = build_self_check_prompt(skill_count=42, threshold=20)
    c = SELF_CHECK_PROMPT_TEMPLATE.format(skill_count=42, threshold=20)
    assert a == b == c


def test_f6_r3_dormant_does_not_count(monkeypatch):
    """cfg.enabled=False 时 should_trigger_self_check 不触发 count."""
    call_count = {"n": 0}

    def _spy_count(*a, **kw):
        call_count["n"] += 1
        return 100

    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        _spy_count,
    )
    cfg = SkillSelfCheckConfig(enabled=False)
    assert should_trigger_self_check(cfg) is False
    assert call_count["n"] == 0  # 没调


def test_f6_r3_does_not_inject_when_below_threshold(monkeypatch):
    """skill_count=20 (boundary) → 19 in cap → 不注入。skill_count=21 → 注入。"""
    cfg = SkillSelfCheckConfig(enabled=True, threshold=20)
    base = "## Base\n"

    # count 短路返回 20 / 19 / 21
    for n in (20, 19):
        monkeypatch.setattr(
            "ascend_op_agent.agent.skill_standards.count_self_built_skills",
            lambda skills_dir=None, task_type=None, _n=n: _n,
        )
        out = render_system_prompt_with_self_check(base, cfg)
        assert "## Skill Self-Check" not in out, f"n={n} should NOT trigger"

    monkeypatch.setattr(
        "ascend_op_agent.agent.skill_standards.count_self_built_skills",
        lambda skills_dir=None, task_type=None: 21,
    )
    out = render_system_prompt_with_self_check(base, cfg)
    assert "## Skill Self-Check" in out  # 21 触发


# ---- F-6.4: constants 收敛性 sanity --------------------------------------


def test_f6_layer6_constants_aligned_with_hermes_default():
    """Plan KTD-3: HERMES_LAYER_LIMIT=10, RECENT_LOADS_MAX=5 — 不准随便改。"""
    assert HERMES_LAYER_LIMIT == 10
    assert RECENT_LOADS_MAX == 5
