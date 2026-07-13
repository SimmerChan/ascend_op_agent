# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""PR-B U4 R3 LLM self-check: 共用 prompt 模板 + dormant trigger helpers.

R3 设计(plan /f177fa6 + Q2 决议):
  - 默认 dormant( ``enable_skill_self_check=False`` 关闭)
  - trigger: self-built skill 数量 > ``SELF_CHECK_TRIGGER_THRESHOLD`` (=20)
  - prompt 共用此模块(``build_self_check_prompt``),PhaseRunner 或将来的
    ``fix_loop`` review node 都能 import 复用,不重复 prompt 字符串
  - post-processing 在 AIAgent.run_conversation 末尾追加 system note,
    让 LLM 主动按已列出的 self-built skill 标准做 self-check(轻量、不阻塞
    当前 turn)

不依赖 LLM 行为,不修改 AIAgent 主循环 —— 仅提供 helper + 钩子(dormant hook
由调用方启用)。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# R3 trigger: skill > 20 才启用(plan Q2 决议).低于阈值 R3 不动,避免小规模
# self-built 反复自检的 token 浪费。
SELF_CHECK_TRIGGER_THRESHOLD = 20


@dataclass
class SkillSelfCheckConfig:
    """R3 self-check 配置(dormant by default)."""

    enabled: bool = False
    threshold: int = SELF_CHECK_TRIGGER_THRESHOLD
    # 可选: 过滤 task_type;None = 全部 self-built 都参与计数。
    # (PhaseRunner 注入 task_type=develop 时只看 develop 类 skill。)
    task_type: Optional[str] = None


def list_self_built_skill_names(
    skills_dir: Optional[str] = None,
) -> list[str]:
    """列出 self-built skill 名(供 trigger 计数用).

    复用 SkillStorage.list_skills()(已 self-built-aware),无文件 I/O 副作用,
    失败时返回 ``[]``(dormant 兼容 — trigger check skip)。

    Args:
        skills_dir: 可选,显式 skills 根目录(测试用)。
    """
    try:
        # 局部 import 避免循环依赖
        from ascend_op_agent.skills.storage import SkillStorage

        if skills_dir is None:
            storage = SkillStorage()
        else:
            storage = SkillStorage(skills_dir=skills_dir)
        return sorted(storage.list_skills())
    except Exception as e:  # noqa: BLE001 - R3 dormant policy
        logger.debug("list_self_built_skill_names failed, returning []: %r", e)
        return []


def count_self_built_skills(
    skills_dir: Optional[str] = None,
    task_type: Optional[str] = None,
) -> int:
    """R3 trigger 计数 — 在 FTS5 SkillsIndex 中查询 task_type 匹配的 skill 数.

    fallback: SkillsIndex 不可用 → 用 SkillStorage.list_skills()(无 task_type
    过滤,粗略计数,但 trigger 决策只需 over/under threshold 的二元判定)。

    Args:
        skills_dir: 可选,显式 skills 目录。
        task_type: 可选,只数该 task_type 的 skills(PhaseRunner 可传)。
    """
    if not task_type:
        # 无 task_type 过滤 → storage 路径(轻量)
        return len(list_self_built_skill_names(skills_dir))

    # task_type 过滤 → SkillsIndex 路径
    try:
        from ascend_op_agent.skills.index import SkillIndex

        idx = SkillIndex()
        try:
            return len(idx.search_by_task_type(task_type))
        finally:
            # SkillIndex() 持有 sqlite3 conn,尽短生命周期;此处不显式 close,
            # 让进程退出回收 — production 测试不关注。
            pass
    except Exception as e:  # noqa: BLE001
        logger.debug("count_self_built_skills via SkillsIndex failed: %r", e)
        return len(list_self_built_skill_names(skills_dir))


def should_trigger_self_check(
    cfg: SkillSelfCheckConfig,
    skill_count: Optional[int] = None,
    skills_dir: Optional[str] = None,
) -> bool:
    """R3 self-check trigger 决策.

    返回 True iff:
      - cfg.enabled is True(上层开关,PhaseRunner / CLI 默认 False → dormant)
      - skill_count > cfg.threshold(>20,plan Q2)
      - skill_count not None

    如果 ``skill_count`` 留 None(默认),内部调 ``count_self_built_skills``
    自行计算(生产路径常用)。

    返回 False 时调用方不应注入 self-check prompt(dormant policy)。
    """
    if not cfg.enabled:
        return False
    if skill_count is None:
        skill_count = count_self_built_skills(
            skills_dir=skills_dir,
            task_type=cfg.task_type,
        )
    return skill_count > cfg.threshold


# R3 prompt 共用模板 ── PhaseRunner 注入 + 将来 fix_loop 注入都用同一份。
# 中文 + 结构化,简短(<200 chars),不污染主系统 Prompt。
SELF_CHECK_PROMPT_TEMPLATE = """## Skill Self-Check (R3)

当前 self-built skill 数量 = {skill_count} (> 阈值 {threshold})。

请在最终提交代码前,按以下顺序基于现有 self-built skill 列表做一轮自检:

1. 重新阅读 PhaseRunner 已注入的 `## Available Skills (self-built)` 列表
2. 对照每条 skill 的 description,确认本次产出符合对应的"项目范围(## Project Scope)"
3. 标出违反 skill 标准的条目作为 follow-up TODO(不阻塞本次提交)

注:这是被动 self-check,不是阻塞性 review。
"""


def build_self_check_prompt(
    skill_count: int,
    threshold: int = SELF_CHECK_TRIGGER_THRESHOLD,
    *,
    extra_context: Optional[dict[str, str]] = None,
) -> str:
    """R3 共用 prompt 渲染 — 单点 source of truth.

    Args:
        skill_count: 当前 self-built skill 数量(用于占位 + 决策记录)
        threshold: 触发阈值(用于占位 + 让 LLM 看到配置值)
        extra_context: 可选扩展(如 phase, task_type, canonical_skill_names)
            — 调用方注入额外 context 占位;模板当前版本忽略,future 扩展点。
    """
    return SELF_CHECK_PROMPT_TEMPLATE.format(
        skill_count=skill_count,
        threshold=threshold,
    )


def render_system_prompt_with_self_check(
    base_prompt: str,
    cfg: SkillSelfCheckConfig,
    skills_dir: Optional[str] = None,
) -> str:
    """PhaseRunner / AIAgent 便利: 在已组装的 system_prompt 末尾追加 R3 self-check
    section(若 trigger 命中)。dormant: 不触发时原样返回 base_prompt。

    拼接策略:R3 段放在 system_prompt **末尾**(Layer 6/7 之后),让 LLM 把
    self-check 当作最终交付时的 checklist,不影响主对话流程。

    Args:
        base_prompt: 已组装好的 7 层 prompt(可能含 cannbot + self-built layer 6)
        cfg: R3 配置(enabled/threshold/task_type)
        skills_dir: 可选测试 override

    Returns:
        加 self-check section 的 prompt 或原 base_prompt
    """
    if not cfg.enabled:
        return base_prompt
    skill_count = count_self_built_skills(
        skills_dir=skills_dir,
        task_type=cfg.task_type,
    )
    if skill_count <= cfg.threshold:
        return base_prompt
    self_check_section = build_self_check_prompt(
        skill_count=skill_count,
        threshold=cfg.threshold,
    )
    return base_prompt + "\n\n" + self_check_section


__all__ = [
    "SELF_CHECK_TRIGGER_THRESHOLD",
    "SkillSelfCheckConfig",
    "list_self_built_skill_names",
    "count_self_built_skills",
    "should_trigger_self_check",
    "build_self_check_prompt",
    "render_system_prompt_with_self_check",
    "SELF_CHECK_PROMPT_TEMPLATE",
]
