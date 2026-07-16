# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""U3: curator status CLI 单测(Tier 0 只读汇总)。

覆盖 plan U3 全部 test scenarios:有 skill+有 usage → 输出计数(R5)、汇总表缺失 → 计数 0
不报错(R6)、汇总表损坏 → 降级计数 0(R6)、无 self-built skill → 空提示(R5)。另验 FE2
(reference flat 目录排除)与按最近活动排序。
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from ascend_op_agent.cli import main
from ascend_op_agent.skills.usage_tracker import UsageTracker


def _make_skill(tmp_path, name):
    """在 tmp/self-built/{name}/ 直接落一个最小 SKILL.md(不经 skill_manage,聚焦 CLI 读路径)。"""
    d = tmp_path / "self-built" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\n\n## body", encoding="utf-8"
    )
    return d


def _run_status(tmp_path):
    runner = CliRunner()
    return runner.invoke(main, ["curator", "--skills-dir", str(tmp_path), "status"])


# ---- R5: 有 skill + 有 usage → 输出计数 ----


def test_status_shows_skill_and_counts(tmp_path):
    _make_skill(tmp_path, "cann-910b3-pitfalls")
    t = UsageTracker(skills_dir=str(tmp_path))
    t.record("cann-910b3-pitfalls", "load")
    t.record("cann-910b3-pitfalls", "load")
    t.record("cann-910b3-pitfalls", "patch")

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    out = result.output
    assert "cann-910b3-pitfalls" in out
    assert "use=2" in out
    assert "patch=1" in out


def test_status_multiple_skills(tmp_path):
    _make_skill(tmp_path, "skill-a")
    _make_skill(tmp_path, "skill-b")
    UsageTracker(skills_dir=str(tmp_path)).record("skill-a", "load")

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    assert "skill-a" in result.output
    assert "skill-b" in result.output


# ---- R6: 汇总表缺失 → 计数 0 不报错 ----


def test_status_missing_usage_shows_zero(tmp_path):
    _make_skill(tmp_path, "lonely")
    assert not (tmp_path / ".usage.json").exists()

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    assert "lonely" in result.output
    assert "use=0" in result.output
    assert "patch=0" in result.output
    assert "未使用" in result.output


# ---- R6: 汇总表损坏 → 降级计数 0 ----


def test_status_corrupt_usage_degrades(tmp_path):
    _make_skill(tmp_path, "brave")
    (tmp_path / ".usage.json").write_text("<<<not json>>>", encoding="utf-8")

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    assert "brave" in result.output
    assert "use=0" in result.output


# ---- R5: 无 self-built skill → 空提示 ----


def test_status_no_skill_empty(tmp_path):
    result = _run_status(tmp_path)
    assert result.exit_code == 0
    assert "无 self-built skill" in result.output


# ---- FE2: reference flat 目录不纳入 self-bbuilt 枚举 ----


def test_status_excludes_reference_flat_dir(tmp_path):
    """list_self_built 只看 self-built/{name}/,排除 {name}_reference 平铺目录(FE2)。"""
    _make_skill(tmp_path, "real-self-built")
    # 造一个 reference 平铺目录(list_skills 会混入,list_self_built 不应)
    ref = tmp_path / "foo_reference"
    ref.mkdir()
    (ref / "SKILL.md").write_text("---\nname: foo\ndescription: r\n---\n\nbody", encoding="utf-8")

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    assert "real-self-built" in result.output
    assert "foo_reference" not in result.output
    assert "use=0" in result.output  # real-self-built 计数 0


# ---- 按最近活动排序(最近在前) ----


def test_status_sorts_by_recent_activity(tmp_path):
    _make_skill(tmp_path, "older")
    _make_skill(tmp_path, "newer")
    # 直接写控制好的时间戳(UsageTracker.record 秒级粒度同秒会并列,不利断言顺序)
    (tmp_path / ".usage.json").write_text(
        json.dumps(
            {
                "older": {"use_count": 1, "last_used_at": "2026-07-10T00:00:00Z"},
                "newer": {"use_count": 1, "last_used_at": "2026-07-15T00:00:00Z"},
            }
        ),
        encoding="utf-8",
    )

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    # newer(最近活动)应排在 older 之前
    assert result.output.index("newer") < result.output.index("older")


def test_status_no_activity_skill_sorts_last(tmp_path):
    _make_skill(tmp_path, "active")
    _make_skill(tmp_path, "dormant")
    (tmp_path / ".usage.json").write_text(
        json.dumps({"active": {"use_count": 3, "last_used_at": "2026-07-15T00:00:00Z"}}),
        encoding="utf-8",
    )

    result = _run_status(tmp_path)
    assert result.exit_code == 0
    # active(有活动)在前,dormant(未使用)在后
    assert result.output.index("active") < result.output.index("dormant")
