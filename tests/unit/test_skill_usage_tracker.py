# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""U1: UsageTracker 汇总表原子读写 + record 接口 + R6 降级单测。

覆盖 plan U1 全部 test scenarios:record use/patch 计数自增 + 时间戳刷新(R3)、
汇总表缺失自动创建 / 损坏 / 形状错降级返空不抛(R6)、原子写无半写中间态(R3)。
"""

from __future__ import annotations

import json

from ascend_op_agent.skills.usage_tracker import UsageTracker


def _tracker(tmp_path):
    return UsageTracker(skills_dir=str(tmp_path))


# ---- record use / patch 计数自增 + 时间戳(R3) ----


def test_record_load_increments_use_count_and_sets_last_used(tmp_path):
    t = _tracker(tmp_path)
    t.record("cann-910b3-pitfalls", "load")
    data = t.load()
    assert data["cann-910b3-pitfalls"]["use_count"] == 1
    assert data["cann-910b3-pitfalls"]["last_used_at"]
    # patch_count 不受 load 影响
    assert data["cann-910b3-pitfalls"]["patch_count"] == 0


def test_record_load_twice_increments_to_two(tmp_path):
    t = _tracker(tmp_path)
    t.record("s", "load")
    t.record("s", "load")
    assert t.load()["s"]["use_count"] == 2


def test_record_patch_increments_patch_count_and_sets_last_activity(tmp_path):
    t = _tracker(tmp_path)
    t.record("s", "patch")
    entry = t.load()["s"]
    assert entry["patch_count"] == 1
    assert entry["last_activity_at"]
    # use_count 不受 patch 影响
    assert entry["use_count"] == 0


def test_record_load_and_patch_independent_on_same_skill(tmp_path):
    t = _tracker(tmp_path)
    t.record("s", "load")
    t.record("s", "load")
    t.record("s", "patch")
    entry = t.load()["s"]
    assert entry["use_count"] == 2
    assert entry["patch_count"] == 1
    assert entry["last_used_at"]
    assert entry["last_activity_at"]


def test_record_unknown_event_is_noop(tmp_path):
    t = _tracker(tmp_path)
    t.record("s", "view")  # unknown → warn + no-op,不抛
    assert t.load() == {}


# ---- R6: 缺失 / 损坏 / 形状错 降级 ----


def test_load_missing_file_returns_empty(tmp_path):
    assert _tracker(tmp_path).load() == {}


def test_record_creates_file_when_missing(tmp_path):
    t = _tracker(tmp_path)
    assert not t.usage_path.exists()
    t.record("s", "load")
    assert t.usage_path.is_file()
    assert t.load()["s"]["use_count"] == 1


def test_load_non_json_corrupt_returns_empty(tmp_path):
    t = _tracker(tmp_path)
    t.usage_path.write_text("{not valid json", encoding="utf-8")
    assert t.load() == {}


def test_record_rebuilds_on_corrupt_non_json(tmp_path):
    t = _tracker(tmp_path)
    t.usage_path.write_text("<<<garbage>>>", encoding="utf-8")
    t.record("s", "load")  # 视为空表重建,不抛
    assert t.load()["s"]["use_count"] == 1


def test_load_top_level_non_dict_returns_empty(tmp_path):
    t = _tracker(tmp_path)
    t.usage_path.write_text("[]", encoding="utf-8")  # 合法 JSON 但顶层是 list
    assert t.load() == {}


def test_load_entry_value_non_dict_is_skipped(tmp_path):
    t = _tracker(tmp_path)
    t.usage_path.write_text(
        json.dumps({"good": {"use_count": 2}, "bad": "not-a-dict"}),
        encoding="utf-8",
    )
    data = t.load()
    assert data == {"good": {"use_count": 2, "patch_count": 0}}


def test_load_coerces_non_int_counts(tmp_path):
    """损坏的计数字段(非 int)coerce 成 0,不抛。"""
    t = _tracker(tmp_path)
    t.usage_path.write_text(
        json.dumps({"s": {"use_count": "oops", "patch_count": None}}),
        encoding="utf-8",
    )
    entry = t.load()["s"]
    assert entry["use_count"] == 0
    assert entry["patch_count"] == 0


# ---- R3: 原子写无半写中间态 ----


def test_atomic_write_leaves_valid_json_no_tmp_leftover(tmp_path):
    """record 后:汇总表是合法 JSON 可被 load() 回读,且无 .usage-*.tmp 残留。"""
    t = _tracker(tmp_path)
    t.record("s", "load")
    # 文件内容是合法 JSON(round-trip 通过)
    raw = t.usage_path.read_text(encoding="utf-8")
    assert json.loads(raw)["s"]["use_count"] == 1
    # 无半写临时文件残留
    leftovers = [p.name for p in tmp_path.glob(".usage-*.tmp")]
    assert leftovers == []


def test_atomic_write_preserves_existing_entries(tmp_path):
    """多次 record 走读改写,既有 skill 条目不丢失。"""
    t = _tracker(tmp_path)
    t.record("a", "load")
    t.record("b", "patch")
    t.record("a", "load")
    data = t.load()
    assert data["a"]["use_count"] == 2
    assert data["b"]["patch_count"] == 1


def test_default_skills_dir_resolves_home():
    """默认 skills_dir 指向 ~/.ascend_op_agent/skills(与 SkillStorage 同源)。"""
    import os

    t = UsageTracker()
    assert str(t.skills_dir) == os.path.expanduser("~/.ascend_op_agent/skills")
    assert t.usage_path.name == ".usage.json"
