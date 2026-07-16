# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Skill 活跃度追踪汇总表 — Skill Curator Lite Tier 0。

单文件 JSON 汇总表(<skills_dir>/.usage.json,默认 ~/.ascend_op_agent/skills/.usage.json),
记录每个 self-built skill 的活跃度计数,作为 STRATEGY.md「Skill reuse rate」metric 的
numerator(分母 defer 到 metric 运营化时定义):

  use_count / last_used_at       —— skill_manage(action="load") 埋点(R1)
  patch_count / last_activity_at —— skill_manage(action="patch") 埋点(R2)

特性:
  - 原子写(tempfile + os.replace):进程崩溃不留半写文件(R3)。并发读要么看到旧文件、
    要么看到新文件,不会读到半写中间态。
  - load() 对 缺失 / 非 JSON / 合法 JSON 但形状错(顶层非 dict、条目值非 dict)一律
    返空 + warn,永不抛 —— 埋点是 best-effort,绝不阻塞 skill_manage 主操作(R6)。
  - 只存「汇总计数」语义(KTD1),非 append-only 事件流(后者才适合 sqlite,如 task_metrics)。

追踪范围限定 self-built skill(R4):tracker 本身不校验名字归属,由调用方(skill_manage,
架构上只触 self-built / cannbot 走独立 CANNBOT_ROOT)保证。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

USAGE_FILE = ".usage.json"

# 合法的活跃事件:load = agent 主动加载(最强使用信号);patch = 作者修订。
EVENTS = ("load", "patch")


def _now_iso() -> str:
    """UTC ISO8601 时间戳(与 skill_manage_tool._provenance 同源)。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class UsageTracker:
    """self-built skill 活跃度汇总表读写原语。

    Args:
        skills_dir: skills 根目录;None → ~/.ascend_op_agent/skills(与 SkillStorage 同源,
            使埋点汇总表与 skill 文件位于同一根目录下)。
    """

    def __init__(self, skills_dir: Any = None):
        self.skills_dir = Path(skills_dir or os.path.expanduser("~/.ascend_op_agent/skills"))
        self.usage_path = self.skills_dir / USAGE_FILE

    def load(self) -> Dict[str, Dict[str, Any]]:
        """读取汇总表。任何异常 / 形状错 → 返空表 + warn(R6,永不抛)。

        防御覆盖(SG1):缺失 / 非 JSON / 合法 JSON 但顶层非 dict(如 ``[]``)/ 条目值非
        dict(如 ``{"x": "str"}``)一律视为空表重建,不抛、不阻塞调用方。
        """
        if not self.usage_path.is_file():
            return {}
        try:
            with open(self.usage_path, "r", encoding="utf-8") as f:
                raw = f.read()
            data = json.loads(raw)
        except Exception as e:  # noqa: BLE001 — 缺失/损坏/编码错统一降级,不区分 JSONDecodeError
            logger.warning(
                "usage summary unreadable (%s): %s; treating as empty", self.usage_path, e
            )
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "usage summary top-level not a dict (%r); treating as empty",
                type(data).__name__,
            )
            return {}
        # 逐条校验形状:值必须是 dict;计数字段 coerce 成 int、时间戳必须是 str。
        clean: Dict[str, Dict[str, Any]] = {}
        for name, entry in data.items():
            if not isinstance(entry, dict):
                logger.warning(
                    "usage entry %r not a dict (%r); skipped", name, type(entry).__name__
                )
                continue
            clean[name] = self._coerce_entry(entry)
        return clean

    @staticmethod
    def _coerce_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        """把单条汇总记录规整成稳定形状(计数 int、时间戳 str)。"""
        out: Dict[str, Any] = {
            "use_count": UsageTracker._as_int(entry.get("use_count")),
            "patch_count": UsageTracker._as_int(entry.get("patch_count")),
        }
        last_used = entry.get("last_used_at")
        last_activity = entry.get("last_activity_at")
        if isinstance(last_used, str):
            out["last_used_at"] = last_used
        if isinstance(last_activity, str):
            out["last_activity_at"] = last_activity
        return out

    @staticmethod
    def _as_int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def record(self, name: str, event: str) -> None:
        """记录一次活跃事件(load → use_count++ / last_used_at;patch → patch_count++ / last_activity_at)。

        原子读改写:``load()``(容错)→ 更新对应字段 → tempfile + os.replace。汇总表
        缺失/损坏会自动重建(R6)。未知 event → warn + no-op(不抛)。真正的 IO 写失败
        (权限/磁盘满)向上抛,由调用方 best-effort try/except 兜底 —— skill_manage 主
        操作不受影响(R6)。
        """
        if event not in EVENTS:
            logger.warning("unknown usage event %r for %r; ignored", event, name)
            return
        data = self.load()
        entry = data.get(name, {})
        entry["use_count"] = int(entry.get("use_count", 0))
        entry["patch_count"] = int(entry.get("patch_count", 0))
        if event == "load":
            entry["use_count"] += 1
            entry["last_used_at"] = _now_iso()
        else:  # patch
            entry["patch_count"] += 1
            entry["last_activity_at"] = _now_iso()
        data[name] = entry
        self._atomic_write(data)

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        """tempfile + os.replace 原子换入(R3)。失败时清理半写临时文件,勿留垃圾。"""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".usage-", suffix=".tmp", dir=str(self.skills_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.usage_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
