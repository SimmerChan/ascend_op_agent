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

"""TaskStore —— 独立 sqlite task 存储(KTD1)。

一期-a tables:tasks / task_threads / tasks_meta(active_task_id)。task_relations
(U5 consumer)与 task_artifacts_index(U6/U8 writer)一期-a 不建(scope S1:
"2nd consumer 才加")。

独立 db 真实理由 = task 层 kill-switch 隔离(R16 abandon 时可整库 drop 不动
CheckpointStore)。镜像 CheckpointStore sqlite 模式(WAL + busy_timeout + IMMEDIATE
防跨进程 SQLITE_BUSY)。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Union

from ascend_op_agent.task_store.models import (
    STATE_NATIVE_NONE,
    TASK_TYPES,
    Task,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    type           TEXT NOT NULL,
    object_payload TEXT NOT NULL DEFAULT '{}',
    state_native   TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_threads (
    task_id   TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (task_id, thread_id)
);

CREATE TABLE IF NOT EXISTS tasks_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_threads_task ON task_threads(task_id);

CREATE TABLE IF NOT EXISTS task_metrics (
    id          TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    from_task   TEXT,
    to_task     TEXT,
    detail      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_metrics_name_time ON task_metrics(metric_name, created_at);
"""

META_ACTIVE_TASK = "active_task_id"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        type=row["type"],
        object_payload=json.loads(row["object_payload"] or "{}"),
        state_native=row["state_native"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TaskStore:
    """独立 sqlite task 存储(KTD1)。每方法 short-lived connection,事务 = 单方法。"""

    DEFAULT_DB_PATH = "~/.ascend_op_agent/tasks.db"

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        # PRAGMA 连接级(busy_timeout/synchronous):_init_schema 只设一次不够,每连接都要
        # —— 否则并发写 BEGIN IMMEDIATE 立即 SQLITE_BUSY 而非等 30s(reliability P2)。
        # journal_mode=WAL 是 db 级持久,留在 _init_schema 即可。
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            c.commit()

    def create_task(self, task_type: str, object_payload: Optional[dict] = None) -> str:
        """建 task(R1)。返 task_id。type 必须在 TASK_TYPES 内。"""
        if task_type not in TASK_TYPES:
            raise ValueError(f"unknown task type: {task_type}; expected one of {TASK_TYPES}")
        task_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        payload_json = json.dumps(object_payload or {}, ensure_ascii=False)
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO tasks (id, type, object_payload, state_native, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (task_id, task_type, payload_json, STATE_NATIVE_NONE, now, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_tasks(self) -> List[Task]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [_row_to_task(r) for r in rows]

    def link_thread(self, task_id: str, thread_id: str) -> None:
        """关联 task ↔ thread(R4:1 task = 1+ threads)。幂等(INSERT OR IGNORE)。

        Raises:
            KeyError: task_id 不存在(防孤儿 task_threads 行 → list_progress KeyError,adversarial)。
        """
        if self.get_task(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
        now = _now_iso()
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT OR IGNORE INTO task_threads (task_id, thread_id, linked_at)"
                    " VALUES (?, ?, ?)",
                    (task_id, thread_id, now),
                )
                c.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now, task_id))
                c.commit()
            except Exception:
                c.rollback()
                raise

    def get_task_threads(self, task_id: str) -> List[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT thread_id FROM task_threads WHERE task_id = ? ORDER BY linked_at",
                (task_id,),
            ).fetchall()
        return [r["thread_id"] for r in rows]

    def set_state_native(self, task_id: str, state_native: str) -> None:
        """设 task-layer-native 标志(paused)。draft 由"无 thread"推导,不存。"""
        now = _now_iso()
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "UPDATE tasks SET state_native = ?, updated_at = ? WHERE id = ?",
                    (state_native, now, task_id),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def set_active(self, task_id: str) -> None:
        """显式选/切 active task(R5,一期-a explicit)。存 tasks_meta。

        Raises:
            KeyError: task_id 不存在(防 phantom active,adversarial/reliability)。
        """
        if self.get_task(task_id) is None:
            raise KeyError(f"unknown task: {task_id}")
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO tasks_meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (META_ACTIVE_TASK, task_id),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def get_active(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT value FROM tasks_meta WHERE key = ?", (META_ACTIVE_TASK,)
            ).fetchone()
        return row["value"] if row is not None else None

    def clear_active(self) -> None:
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("DELETE FROM tasks_meta WHERE key = ?", (META_ACTIVE_TASK,))
                c.commit()
            except Exception:
                c.rollback()
                raise

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """读 tasks_meta 任意 key(通用原语;active 用专用 get_active)。"""
        with self._conn() as c:
            row = c.execute("SELECT value FROM tasks_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else default

    def set_meta(self, key: str, value: str) -> None:
        """写 tasks_meta 任意 key(upsert;active 用专用 set_active,后者带 task 存在性校验)。"""
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO tasks_meta (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise

    def record_metric(
        self,
        metric_name: str,
        from_task: Optional[str] = None,
        to_task: Optional[str] = None,
        detail: Optional[dict] = None,
    ) -> str:
        """记一条 dogfood metric 事件(R16 gate 自动采集)。append-only,返 metric id。

        metric_name 如 "spontaneous_task_switch" / "context_juggling_complaint"。
        from_task/to_task 用于 switch 事件溯源;detail 存自由 JSON(如抱怨描述)。
        不与 task CRUD 耦合(set_active 行为不变),纯追加供 falsifier 汇总。
        """
        mid = uuid.uuid4().hex
        now = _now_iso()
        detail_json = json.dumps(detail or {}, ensure_ascii=False)
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO task_metrics (id, metric_name, from_task, to_task, detail, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (mid, metric_name, from_task, to_task, detail_json, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return mid

    def list_metrics(
        self,
        metric_name: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[dict]:
        """查 metric 事件(falsifier 汇总用)。

        metric_name=None 全部;since(ISO) 过滤 created_at >= since。返回按 created_at 升序。
        """
        query = (
            "SELECT id, metric_name, from_task, to_task, detail, created_at" " FROM task_metrics"
        )
        clauses: List[str] = []
        params: List[str] = []
        if metric_name is not None:
            clauses.append("metric_name = ?")
            params.append(metric_name)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        with self._conn() as c:
            rows = c.execute(query, params).fetchall()
        return [
            {
                "id": r["id"],
                "metric_name": r["metric_name"],
                "from_task": r["from_task"],
                "to_task": r["to_task"],
                "detail": json.loads(r["detail"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
