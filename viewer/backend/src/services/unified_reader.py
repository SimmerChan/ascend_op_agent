"""Unified session reader - 合并 SessionRecordManager(聊天)+ CheckpointStore(工作流)
成统一会话列表。

方向 A(viewer 聚合):不改变两个存储,只在读取层合并。viewer 单 tab 展示统一列表,
详情按 source 路由(聊天调 sessions API,工作流调 checkpoints API)。
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)


def _to_ts(s) -> float:
    """统一 updated_at 成 float timestamp(秒)。聊天是 float,工作流是 ISO str。"""
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _to_iso(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def list_unified_sessions(limit: int = 200) -> list[dict]:
    """合并聊天(sessions JSONL)+ 工作流(checkpoints SQLite)成统一会话列表。

    按 updated_at desc 排序。单个存储坏不阻塞另一个。
    """
    items: list[dict] = []

    # 聊天(SessionRecordManager JSONL)
    try:
        from services.tree_builder import TreeBuilder

        tb = TreeBuilder()
        for s in tb.list_sessions(limit):
            ts = _to_ts(s.get("created_at"))
            items.append(
                {
                    "id": s.get("session_id", ""),
                    "source": "chat",
                    "label": str(s.get("session_id", ""))[:8],
                    "updated_at": ts,
                    "updated_at_display": _to_iso(ts),
                    "phase": None,
                    "status": None,
                    "db_file": None,
                    "entry_count": s.get("entry_count", 0),
                }
            )
    except Exception as e:
        logger.warning("list chat sessions failed: %s", e)

    # 工作流(CheckpointStore SQLite:生产 op: + spike + e2e + migration)
    try:
        from services.ckpt_reader import list_all_checkpoints

        for c in list_all_checkpoints():
            ts = _to_ts(c.get("updated_at"))
            items.append(
                {
                    "id": c.get("thread_id", ""),
                    "source": c.get("source", "other"),
                    "label": str(c.get("thread_id", ""))[:12],
                    "updated_at": ts,
                    "updated_at_display": c.get("updated_at") or _to_iso(ts),
                    "phase": c.get("current_phase"),
                    "status": c.get("status"),
                    "db_file": c.get("db_file"),
                    "entry_count": 0,
                }
            )
    except Exception as e:
        logger.warning("list checkpoints failed: %s", e)

    # 按 updated_at desc
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    if limit:
        items = items[:limit]
    return items
