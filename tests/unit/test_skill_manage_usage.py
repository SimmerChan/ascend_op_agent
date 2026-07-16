# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""U2: skill_manage load/patch 活跃度埋点(Tier 0)单测。

覆盖 plan U2 全部 test scenarios:load 已存在 → use_count=1(R1)、patch → patch_count=1
(R2,共用路径 is_update gate)、load 不存在 → 不埋点(R1)、tracker 抛异常 → skill_manage
仍成功(R6 best-effort)、create() 不增 patch_count(FE1)、cannbot vendor 不可达 → 不埋点
(R4 由架构保证)。SkillStorage + UsageTracker 双重重定向到 tmp,保证 hermetic。
"""

from __future__ import annotations

from ascend_op_agent.agent.tools.skill_manage_tool import skill_manage
from ascend_op_agent.skills.usage_tracker import UsageTracker

_BODY = """## Project Scope

Applies to Ascend 910B3 / CANN 9.1.0 operator compile.

## Pitfalls

- msopgen compile replaces the assumed cann_compile binary.
"""


def _setup(tmp_path, monkeypatch):
    """把 SkillStorage() 与 UsageTracker() 都重定向到 tmp_path(hermetic)。"""
    from ascend_op_agent.skills import storage as storage_mod
    from ascend_op_agent.skills import usage_tracker as usage_mod

    _orig_st = storage_mod.SkillStorage.__init__
    monkeypatch.setattr(
        storage_mod.SkillStorage,
        "__init__",
        lambda self, skills_dir=None: _orig_st(self, skills_dir=str(tmp_path)),
    )
    _orig_ut = usage_mod.UsageTracker.__init__
    monkeypatch.setattr(
        usage_mod.UsageTracker,
        "__init__",
        lambda self, skills_dir=None: _orig_ut(self, skills_dir=str(tmp_path)),
    )
    return tmp_path


def _create_skill(name="tiling-pitfalls"):
    return skill_manage(
        action="create",
        name=name,
        description="910B3 compile pitfalls",
        task_type="develop",
        topic="compile",
        body=_BODY,
    )


def _usage(tmp_path):
    return UsageTracker(skills_dir=str(tmp_path)).load()


# ---- R1: load 已存在 → use_count ----


def test_load_existing_records_use_count(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _create_skill()
    res = skill_manage(action="load", skill_name="tiling-pitfalls")
    assert res["success"] is True
    entry = _usage(tmp_path)["tiling-pitfalls"]
    assert entry["use_count"] == 1
    assert entry["last_used_at"]


def test_load_nonexistent_no_tracking(tmp_path, monkeypatch):
    """load 不存在(not_found)→ 无成功加载,不埋点(R1)。"""
    _setup(tmp_path, monkeypatch)
    res = skill_manage(action="load", skill_name="ghost")
    assert res["success"] is False
    assert _usage(tmp_path) == {}


# ---- R2: patch → patch_count;create(is_update=False)不误增 ----


def test_patch_records_patch_count(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _create_skill()
    res = skill_manage(
        action="patch",
        name="tiling-pitfalls",
        description="910B3 compile pitfalls v2",
        task_type="develop",
        topic="compile",
        body=_BODY,
    )
    assert res["success"] is True
    entry = _usage(tmp_path)["tiling-pitfalls"]
    assert entry["patch_count"] == 1
    assert entry["last_activity_at"]
    # patch 不误增 use_count
    assert entry["use_count"] == 0


def test_create_does_not_increment_patch_count(tmp_path, monkeypatch):
    """_save_self_built 是 create+patch 共用路径:create(is_update=False)不埋点(FE1)。"""
    _setup(tmp_path, monkeypatch)
    _create_skill()
    entry = _usage(tmp_path).get("tiling-pitfalls")
    # create 不计 patch_count(也不计 use_count —— create 不是 load/patch 事件)
    assert entry is None


# ---- R6: tracker 抛异常 → skill_manage 仍成功(best-effort) ----


def test_skill_manage_succeeds_when_tracker_raises(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _create_skill()
    # 让 record 真炸:_record_usage 应吞掉异常,skill_manage 照常成功
    from ascend_op_agent.skills import usage_tracker as usage_mod

    def _boom(self, name, event):
        raise RuntimeError("tracker disk on fire")

    monkeypatch.setattr(usage_mod.UsageTracker, "record", _boom)
    res = skill_manage(action="load", skill_name="tiling-pitfalls")
    assert res["success"] is True
    # patch 同理
    res2 = skill_manage(
        action="patch",
        name="tiling-pitfalls",
        description="x",
        task_type="develop",
        topic="compile",
        body=_BODY,
    )
    assert res2["success"] is True


# ---- R4: cannbot vendor skill 不可达 → 不埋点(架构保证) ----


def test_cannbot_vendor_unreachable_not_tracked(tmp_path, monkeypatch):
    """cannbot 走独立 CANNBOT_ROOT,skill_manage.load_skill 只搜 flat+self-built,接触不到
    (R4 由架构保证)。把一个"vendor"skill 放在 skills_dir 外的独立目录,确认 load 找不到、
    不埋点 —— 复现 cannbot 不可达。"""
    _setup(tmp_path, monkeypatch)
    # 模拟 cannbot vendor root(独立于 skills_dir)
    vendor = tmp_path / "cannbot-vendor" / "ops-summary"
    vendor.mkdir(parents=True)
    (vendor / "SKILL.md").write_text(
        "---\nname: ops-summary\ndescription: cannbot vendor skill\n---\n\nbody",
        encoding="utf-8",
    )
    # skill_manage 的 storage 是 tmp_path(作 skills_dir),不会去 cannbot-vendor 找
    res = skill_manage(action="load", skill_name="ops-summary")
    assert res["success"] is False  # not_found
    assert _usage(tmp_path) == {}  # 未触及、未埋点
