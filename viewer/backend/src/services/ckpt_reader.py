"""Checkpoint reader service —— 遍历 ``~/.ascend_op_agent/`` 下所有 checkpoint db,
聚合 thread 列表 + 按 ``(db_file, thread_id)`` 读完整 state 快照。

读两类 db:
- 顶层 ``~/.ascend_op_agent/checkpoints.db`` —— 生产 op: 路径(CheckpointStore 默认)
- ``~/.ascend_op_agent/checkpoints/*.db`` —— spike / e2e 脚本(scripts 改路径后)

viewer 用此 service 看到所有算子开发数据(生产 op: + spike + e2e),补 sessions
router(只读 SessionRecordManager JSONL 聊天事件流)覆盖不到的算子开发路径。

安全:``db_file`` 参数只在已知的 db 文件名集合里匹配(路径名白名单),防路径穿越。
"""

import logging
import sys
from pathlib import Path
from typing import Optional

# 项目根(同 tree_builder.py 的 path 注入方式)
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from ascend_op_agent.orchestrator.checkpoint import CheckpointStore  # noqa: E402

logger = logging.getLogger(__name__)

AGENT_DIR = Path("~/.ascend_op_agent").expanduser()
TOP_LEVEL_DB = AGENT_DIR / "checkpoints.db"
CHECKPOINTS_SUBDIR = AGENT_DIR / "checkpoints"


def source_from_filename(filename: str) -> str:
    """从 db 文件名推断来源(spike_/e2e_ 前缀;checkpoints.db = 生产 op:)。"""
    name = filename.lower()
    if name == "checkpoints.db":
        return "production"
    if name.startswith("spike_"):
        return "spike"
    if name.startswith("e2e_"):
        return "e2e"
    return "other"


def discover_db_files() -> list[tuple[str, Path]]:
    """返回 ``[(db_file filename, full_path), ...]`` 所有 checkpoint db。

    顶层生产 db 在前,子目录 spike/e2e db 按文件名排序在后。
    """
    result: list[tuple[str, Path]] = []
    if TOP_LEVEL_DB.exists():
        result.append((TOP_LEVEL_DB.name, TOP_LEVEL_DB))
    if CHECKPOINTS_SUBDIR.is_dir():
        for p in sorted(CHECKPOINTS_SUBDIR.glob("*.db")):
            result.append((p.name, p))
    return result


def list_all_checkpoints() -> list[dict]:
    """遍历所有 db,聚合 thread 列表。每条带 ``db_file`` + ``source``。

    单个 db 坏不阻塞其他(记录 error 项继续)。按 ``updated_at`` desc 排序。
    """
    items: list[dict] = []
    for db_file, full_path in discover_db_files():
        try:
            store = CheckpointStore(full_path)
            for pc in store.list_all_threads():
                items.append(
                    {
                        "db_file": db_file,
                        "thread_id": pc.thread_id,
                        "current_phase": pc.current_phase,
                        "status": pc.status,
                        "updated_at": pc.updated_at,
                        "source": source_from_filename(db_file),
                    }
                )
        except Exception as e:
            logger.warning("Failed to read checkpoint db %s: %s", full_path, e)
            items.append(
                {
                    "db_file": db_file,
                    "thread_id": f"<unreadable>",
                    "current_phase": None,
                    "status": "error",
                    "updated_at": "",
                    "source": source_from_filename(db_file),
                }
            )
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return items


def _resolve_db_path(db_file: str) -> Optional[Path]:
    """``db_file``(纯文件名)→ full_path。

    只允许 ``discover_db_files()`` 已知的文件名 —— 路径名白名单防 ``db_file=../../etc/passwd``。
    """
    allowed = {name: path for name, path in discover_db_files()}
    return allowed.get(db_file)


def get_checkpoint_state(db_file: str, thread_id: str) -> Optional[dict]:
    """读指定 ``(db_file, thread_id)`` 的完整 state。

    Returns:
        ``{state, status, updated_at, current_phase, source}`` 或 None(不存在)。
        ``state`` 是 state_json 解析后的 dict(含 messages/code_result/compile_result/
        precision_report 等 15 顶层字段);status/updated_at 来自 checkpoints 表列
        (state_json 不含,从 list_all_threads 补)。
    """
    full_path = _resolve_db_path(db_file)
    if full_path is None or not full_path.exists():
        return None
    store = CheckpointStore(full_path)
    state = store.load_and_migrate_checkpoint(thread_id)
    if state is None:
        return None
    # state_json 不含 status/updated_at(那是表列),从 list_all_threads 补
    meta = {
        "status": None,
        "updated_at": None,
        "current_phase": state.get("current_phase"),
    }
    for pc in store.list_all_threads():
        if pc.thread_id == thread_id:
            meta["status"] = pc.status
            meta["updated_at"] = pc.updated_at
            meta["current_phase"] = pc.current_phase
            break
    return {"state": state, "source": source_from_filename(db_file), **meta}
