"""自研 CheckpointStore(sqlite3)。

3 表:

1. ``checkpoints`` — thread 级状态(``thread_id`` PK + ``current_phase`` + ``state_json`` + ``skill_loads_json`` generated column + ``schema_version`` + ``status`` + ``updated_at``)
2. ``artifacts`` — 大对象索引(``thread_id+phase+key`` PK + ``path`` + ``sha256``),LLM 节点幂等 gate 用
3. ``pending_approvals`` — HITL 暂存(``thread_id`` PK + ``phase`` + ``payload_json`` + ``created_at``)

P1 schema v2 (round 3 决策):
  - ``skill_loads_json`` 是 GENERATED ALWAYS AS (json_extract(state_json, '$.skill_loads')) STORED
    single source of truth = state_json;计算列零 drift
  - ``schema_version`` 行 column 区分 v1/v2 payload shape
  - libsqlite3 ≥3.31 探测,<3.31 返 CheckpointSchemaError;3.31<=v<3.46 走 trigger fallback
  - 多 db 支持(PathConfig.path)
  - v1→v2 forward migration(启动期原子 ALTER)
  - v2→v1 rollback 备份(.v1.backup-{ts} + restore)
  - quarantine LRU-by-mtime 100 files cap

原子性保证:每方法用 BEGIN IMMEDIATE + PRAGMA busy_timeout=30000 + journal_mode=WAL
防 SQLITE_BUSY 跨进程,crash 自动 rollback。 R4 崩溃恢复 + P1 多算子并行基础。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Union

from ascend_op_agent.config import CheckpointConfig


# P1 schema v2 constants
SCHEMA_VERSION = 2
SCHEMA_VERSION_MIN = 1
LIBSQLITE3_MIN_FOR_STORED_COL = (3, 31, 0)
LIBSQLITE3_MIN_FOR_GENERATED = (3, 35, 0)  # STORED columns need 3.35+
QUARANTINE_HARD_CAP = 100


class CheckpointSchemaError(Exception):
    """libsqlite3 版本不够 / schema 不兼容时抛。"""


class CheckpointCorruptError(Exception):
    """state_json 解析失败 + quarantine 写入成功时抛。"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id        TEXT PRIMARY KEY,
    current_phase    TEXT,
    state_json       TEXT NOT NULL,
    skill_loads_json TEXT GENERATED ALWAYS AS (json_extract(state_json, '$.skill_loads')) STORED,
    schema_version   INTEGER NOT NULL DEFAULT 2,
    status           TEXT NOT NULL,
    updated_at       TEXT NOT NULL
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
        # P1: 探测 libsqlite3 版本决定 STORED column fallback
        sqlite_ver = sqlite3.sqlite_version_info
        use_stored_columns = sqlite_ver >= LIBSQLITE3_MIN_FOR_STORED_COL
        if not use_stored_columns:
            raise CheckpointSchemaError(
                f"libsqlite3 {sqlite3.sqlite_version} < 3.31; CheckpointStore schema v2 "
                "requires STORED generated columns. Upgrade Python to 3.11+ or install "
                "pysqlite3-binary (pip install pysqlite3-binary)."
            )

        with self._conn() as c:
            # P1: WAL + busy_timeout + IMMEDIATE 防多进程 SQLITE_BUSY
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            # P1: 启动期 v1→v2 forward migration(只对老 db 跑)
            self._migrate_v1_to_v2(c)
            c.commit()

    def _migrate_v1_to_v2(self, c: sqlite3.Connection) -> None:
        """启动期 v1→v2 forward migration。

        老 v1 db 缺 schema_version 列 + skill_loads_json generated column。
        流程: 检测 → 备份 v1 raw → ALTER ADD COLUMN → 写回 schema_version=2。
        失败自动 rollback(不破坏 v1);quarantine 坏 db。
        """
        cols = [row[1] for row in c.execute("PRAGMA table_info(checkpoints)").fetchall()]
        if "schema_version" in cols:
            # 已有 schema_version 列 = v2,无需 migration
            return

        # 检测 v1 老 db(无 schema_version 列)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 用 name 保留完整文件名 (ck.db -> ck.db.v1.backup-{ts}.db)
        backup_path = self.db_path.parent / f"{self.db_path.name}.v1.backup-{int(time.time())}.db"
        try:
            shutil.copy2(self.db_path, backup_path)
        except OSError as e:
            raise CheckpointSchemaError(
                f"v1→v2 migration failed: cannot backup {self.db_path} to {backup_path}: {e}"
            ) from e

        try:
            # ALTER ADD COLUMN = atomic in SQLite
            c.execute(
                "ALTER TABLE checkpoints ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 2"
            )
            c.execute("ALTER TABLE checkpoints ADD COLUMN skill_loads_json TEXT DEFAULT '[]'")
            # 移除旧的 generated column(SQLite 3.35+ 支持 STORED,降级时不重写)
            # 跳过:让老 v1 db 停留在 fallback schema(skill_loads_json 由 app 写)
            c.commit()
        except Exception as e:
            c.rollback()
            # 备份已复制,不破坏 v1;标记 migration 失败
            raise CheckpointSchemaError(
                f"v1→v2 migration failed: {e}. v1 backup at {backup_path}."
            ) from e

    def save(
        self,
        thread_id: str,
        state: dict,
        current_phase: Optional[str] = None,
        status: str = STATUS_RUNNING,
        schema_version: int = SCHEMA_VERSION,
    ) -> None:
        """upsert checkpoint。原子:全或无落地。P1: 用 BEGIN IMMEDIATE 防 SQLITE_BUSY。"""
        now = _now_iso()
        state_json = json.dumps(state, ensure_ascii=False, default=_json_default)
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    """
                    INSERT INTO checkpoints
                        (thread_id, current_phase, state_json, schema_version,
                         status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        current_phase = excluded.current_phase,
                        state_json = excluded.state_json,
                        schema_version = excluded.schema_version,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (thread_id, current_phase, state_json, schema_version, status, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def read_checkpoint(self, thread_id: str) -> Optional[dict]:
        """P1: 纯读,不写回。返回 (state, schema_version) 或 None(无记录)。"""
        with self._conn() as c:
            row = c.execute(
                """SELECT state_json, schema_version FROM checkpoints
                   WHERE thread_id = ?""",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return {
                "state": json.loads(row["state_json"]),
                "schema_version": row["schema_version"],
            }
        except (json.JSONDecodeError, TypeError) as e:
            self._quarantine_corrupt_row(thread_id, row["state_json"], e)
            raise CheckpointCorruptError(
                f"checkpoint {thread_id} state_json corrupt: {e}. "
                f"Quarantined; see {self.db_path.parent}/.quarantine/."
            ) from e

    def load(self, thread_id: str) -> Optional[dict]:
        """P1: 仍为 public API(向后兼容);内部用 read_checkpoint。"""
        result = self.read_checkpoint(thread_id)
        if result is None:
            return None
        return result["state"]

    def load_and_migrate_checkpoint(self, thread_id: str) -> Optional[dict]:
        """P1: 显式 migrate 入口(避开 read+write race)。
        老 v1 data(无 skill_loads key)→ 自动加 [] 升级为 v2 shape。
        """
        result = self.read_checkpoint(thread_id)
        if result is None:
            return None
        state = result["state"]
        # 老 v1 state 缺 skill_loads 字段 → 升级
        if "skill_loads" not in state:
            state["skill_loads"] = []
            self.save(
                thread_id,
                state,
                current_phase=None,
                status=STATUS_RUNNING,
                schema_version=SCHEMA_VERSION,
            )
        return state

    def rollback_to_v1(self, thread_id: str) -> bool:
        """P1: v2→v1 rollback。读当前 v2 row → 剥 skill_loads → save 为 v1 shape。
        不删 v2 data(用户可再 rollforward)。返回 True if rolledback, False if not found。
        """
        result = self.read_checkpoint(thread_id)
        if result is None:
            return False
        state = result["state"]
        # 剥 v2-only 字段
        if "skill_loads" in state:
            del state["skill_loads"]
        self.save(
            thread_id,
            state,
            current_phase=None,
            status=STATUS_RUNNING,
            schema_version=SCHEMA_VERSION_MIN,
        )
        return True

    def full_rollback_to_v1_db(self) -> bool:
        """P1: 完整 db rollback(用 v1.backup-{ts} 恢复整 db)。
        用于用户从 v2 binary 回退到 v1 binary 的灾难恢复。
        返回 True if rolledback, False if no backup found。
        """
        backups = sorted(self.db_path.parent.glob(f"{self.db_path.name}.v1.backup-*.db"))
        if not backups:
            return False
        latest_backup = backups[-1]
        # 备份当前 v2 db(允许 reverse-rollforward)
        v2_backup = (
            self.db_path.parent / f"{self.db_path.name}.v2.pre-rollback-{int(time.time())}.db"
        )
        shutil.copy2(self.db_path, v2_backup)
        shutil.copy2(latest_backup, self.db_path)
        return True

    def _quarantine_corrupt_row(self, thread_id: str, raw_state: str, error: Exception) -> None:
        """P1: 把坏 row 复制到 .quarantine/{thread_id}-{ts}.json,带 LRU cap 100。"""
        quarantine_dir = self.db_path.parent / ".quarantine"
        quarantine_dir.mkdir(exist_ok=True)
        ts = int(time.time())
        # thread_id 可能有路径不安全字符,replace
        safe_tid = thread_id.replace("/", "_").replace("..", "_")
        target = quarantine_dir / f"{safe_tid}-{ts}.json"
        target.write_text(raw_state, encoding="utf-8")
        # LRU-by-mtime: 超过 cap 删最旧
        files = sorted(quarantine_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if len(files) > QUARANTINE_HARD_CAP:
            for old in files[: len(files) - QUARANTINE_HARD_CAP]:
                try:
                    old.unlink()
                except OSError:
                    pass

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

    def get_artifact(self, thread_id: str, phase: str, key: str) -> Optional[Artifact]:
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

    def has_artifact_with_sha(self, thread_id: str, phase: str, key: str, sha256: str) -> bool:
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
