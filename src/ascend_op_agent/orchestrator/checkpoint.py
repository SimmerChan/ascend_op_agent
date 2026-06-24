"""自研 CheckpointStore(sqlite3)。

3 表:

1. ``checkpoints`` — thread 级状态(``thread_id`` PK + ``current_phase`` + ``state_json`` + ``status`` + ``updated_at``)
2. ``artifacts`` — 大对象索引(``thread_id+phase+key`` PK + ``path`` + ``sha256``),LLM 节点幂等 gate 用
3. ``pending_approvals`` — HITL 暂存(``thread_id`` PK + ``phase`` + ``payload_json`` + ``created_at``)

原子性保证:每方法在 sqlite transaction 内,``commit()`` 才落地,异常 ``rollback()``。
crash 不留半截记录 —— 这是 R4 崩溃恢复的基础。

P0 单算子顺序执行无并发;P1 多算子并行再加 WAL + 应用层锁。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from ascend_op_agent.config import CheckpointConfig


SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id     TEXT PRIMARY KEY,
    current_phase TEXT,
    state_json    TEXT NOT NULL,
    status        TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    thread_id  TEXT NOT NULL,
    phase      TEXT NOT NULL,
    key        TEXT NOT NULL,
    path       TEXT,
    sha256     TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, phase, key)
);

CREATE TABLE IF NOT EXISTS pending_approvals (
    thread_id    TEXT PRIMARY KEY,
    phase        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_status ON checkpoints(status);
"""


STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_WAITING_CONFIRM = "waiting_confirm"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


@dataclass
class PendingCheckpoint:
    """``list_pending`` 返回的轻量记录(只含元数据,不含 state_json)。"""

    thread_id: str
    current_phase: Optional[str]
    status: str
    updated_at: str


@dataclass
class Artifact:
    thread_id: str
    phase: str
    key: str
    path: Optional[str]
    sha256: Optional[str]
    updated_at: str


@dataclass
class PendingApproval:
    """``consume_pending`` 返回的完整 pending 记录。"""

    phase: str
    payload: dict


class CheckpointStore:
    """sqlite3 单文件 checkpoint 存储。

    每方法开 short-lived connection(``check_same_thread=False`` 允许 PhaseRunner
    线程池调用)。事务边界 = 单个方法调用。
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @classmethod
    def from_config(cls, config: CheckpointConfig) -> "CheckpointStore":
        return cls(config.db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)
            c.commit()

    def save(
        self,
        thread_id: str,
        state: dict,
        current_phase: Optional[str] = None,
        status: str = STATUS_RUNNING,
    ) -> None:
        """upsert checkpoint。原子:全或无落地。"""
        now = _now_iso()
        state_json = json.dumps(state, ensure_ascii=False, default=_json_default)
        with self._conn() as c:
            try:
                c.execute(
                    """
                    INSERT INTO checkpoints
                        (thread_id, current_phase, state_json, status, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        current_phase = excluded.current_phase,
                        state_json = excluded.state_json,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (thread_id, current_phase, state_json, status, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def load(self, thread_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT state_json FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["state_json"])

    def get_status(self, thread_id: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT status FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return row["status"] if row is not None else None

    def list_pending(self) -> list[PendingCheckpoint]:
        """列出 status != 'done' 的 checkpoint(启动时 resume 检测用)。"""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT thread_id, current_phase, status, updated_at
                FROM checkpoints
                WHERE status != ?
                ORDER BY updated_at ASC
                """,
                (STATUS_DONE,),
            ).fetchall()
        return [
            PendingCheckpoint(
                thread_id=r["thread_id"],
                current_phase=r["current_phase"],
                status=r["status"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def mark_waiting(
        self,
        thread_id: str,
        phase: str,
        payload: dict,
    ) -> None:
        """HITL 暂停:status=waiting_confirm + pending_approvals 存 payload。

        原子:checkpoint 状态更新和 pending 插入在同一 transaction。
        """
        now = _now_iso()
        payload_json = json.dumps(payload, ensure_ascii=False, default=_json_default)
        with self._conn() as c:
            try:
                c.execute(
                    "UPDATE checkpoints SET status = ?, updated_at = ? WHERE thread_id = ?",
                    (STATUS_WAITING_CONFIRM, now, thread_id),
                )
                c.execute(
                    """
                    INSERT INTO pending_approvals (thread_id, phase, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        phase = excluded.phase,
                        payload_json = excluded.payload_json,
                        created_at = excluded.created_at
                    """,
                    (thread_id, phase, payload_json, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def consume_pending(self, thread_id: str) -> Optional[PendingApproval]:
        """读 pending payload 并删除 + status 回 running。原子。

        Returns:
            PendingApproval(phase, payload),无 pending 返回 None。
        """
        with self._conn() as c:
            try:
                row = c.execute(
                    "SELECT phase, payload_json FROM pending_approvals WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if row is None:
                    return None
                c.execute(
                    "DELETE FROM pending_approvals WHERE thread_id = ?",
                    (thread_id,),
                )
                c.execute(
                    "UPDATE checkpoints SET status = ?, updated_at = ? WHERE thread_id = ?",
                    (STATUS_RUNNING, _now_iso(), thread_id),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return PendingApproval(
            phase=row["phase"],
            payload=json.loads(row["payload_json"]),
        )

    def save_artifact(
        self,
        thread_id: str,
        phase: str,
        key: str,
        path: Optional[Union[str, Path]] = None,
        content: Optional[bytes] = None,
    ) -> Optional[str]:
        """存 artifact。``path`` 或 ``content`` 二选一。

        - 给 ``path``:计算该文件 sha256
        - 给 ``content``:计算 bytes sha256(``path`` 留空)
        - 都不给:sha256 留空(仅记录 key 存在)

        Returns:
            sha256 字符串(无内容时 None)
        """
        sha256: Optional[str] = None
        path_str = str(path) if path is not None else None
        if content is not None:
            sha256 = hashlib.sha256(content).hexdigest()
        elif path is not None:
            sha256 = _sha256_of_file(Path(path))

        now = _now_iso()
        with self._conn() as c:
            try:
                c.execute(
                    """
                    INSERT INTO artifacts (thread_id, phase, key, path, sha256, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id, phase, key) DO UPDATE SET
                        path = excluded.path,
                        sha256 = excluded.sha256,
                        updated_at = excluded.updated_at
                    """,
                    (thread_id, phase, key, path_str, sha256, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return sha256

    def get_artifact(
        self, thread_id: str, phase: str, key: str
    ) -> Optional[Artifact]:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT thread_id, phase, key, path, sha256, updated_at
                FROM artifacts
                WHERE thread_id = ? AND phase = ? AND key = ?
                """,
                (thread_id, phase, key),
            ).fetchone()
        if row is None:
            return None
        return Artifact(
            thread_id=row["thread_id"],
            phase=row["phase"],
            key=row["key"],
            path=row["path"],
            sha256=row["sha256"],
            updated_at=row["updated_at"],
        )

    def has_artifact_with_sha(
        self, thread_id: str, phase: str, key: str, sha256: str
    ) -> bool:
        """幂等 gate:相同 sha256 的 artifact 是否已存在。

        LLM 节点 resume 时:若 sha 已存在则跳过重生成(避免覆盖前次产物)。
        这是 P0-3 修正的 idempotency 契约底层支撑。
        """
        with self._conn() as c:
            row = c.execute(
                """
                SELECT 1 FROM artifacts
                WHERE thread_id = ? AND phase = ? AND key = ? AND sha256 = ?
                """,
                (thread_id, phase, key, sha256),
            ).fetchone()
        return row is not None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_of_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_default(o: Any) -> Any:
    """OpState 嵌入的 dataclass 已在调用方转 dict;此处仅兜底 Path/set/bytes。"""
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", errors="replace")
    raise TypeError(f"Cannot serialize {type(o).__name__}")
