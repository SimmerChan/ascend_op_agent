# Copyright 2026 SimperChan
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

"""U5 task_relations —— 邻接表(KTD9)。

typed graph 边表,落在 tasks.db(与 tasks/task_threads 同库,KTD1 隔离域)。一期
relation_type ∈ {spawned-by, depends-on};两类各成独立 DAG(cycle detection
**per relation_type** —— spawned-by 与 depends-on 互不干扰)。

schema(KTD9):
    ``(src_task_id, dst_task_id, relation_type, confidence, created_at,
       created_by, updated_at)``
    PK = ``(src_task_id, dst_task_id, relation_type)`` —— 同 src→dst 同 type 幂等
    索引 = ``(src_task_id)`` + ``(dst_task_id, relation_type)``

语义约定(语义非强制):
- ``spawned-by``:src_task 由 dst_task 派生(AE3 迁移中 spawn analyze)。
- ``depends-on``:src_task 依赖 dst_task 产物(AE6 optimize depends-on analyze)。

#10(本 plan 融入的 P2)#10 指出 KTD9「FK 不强制」+ task_id 可能复用 → depends-on
读权限误授权。**验证结论**:TaskStore.create_task 用 ``uuid.uuid4().hex[:12]``
生成 task_id(随机 hex 截断,非顺序 INTEGER rowid),id **永不复用** —— 删 task
(一期-a 未实现 delete)后该 id 也不会被新 task 拿到。故 task_id 复用导致的读权限
误授权风险天然消解,FK 不强制是安全的。本表 FK 不强制只为允许 dangling audit 记录
(task 终态删除时 relation 保留作 audit trail),非为 id 复用。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple, Union

# ---- relation 类型常量(R2 一期集) ----
RELATION_SPAWNED_BY = "spawned-by"
RELATION_DEPENDS_ON = "depends-on"
RELATION_TYPES: Tuple[str, ...] = (RELATION_SPAWNED_BY, RELATION_DEPENDS_ON)

# created_by 取值
CREATED_BY_MANUAL = "manual"
CREATED_BY_LLM = "llm"

_RELATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_relations (
    src_task_id   TEXT NOT NULL,
    dst_task_id   TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    confidence    REAL,
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (src_task_id, dst_task_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_task_relations_src ON task_relations(src_task_id);
CREATE INDEX IF NOT EXISTS idx_task_relations_dst_type ON task_relations(dst_task_id, relation_type);
"""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Relation:
    """task_relations 一行。"""

    src_task_id: str
    dst_task_id: str
    relation_type: str
    confidence: Optional[float]
    created_at: str
    created_by: str
    updated_at: str


def _row_to_relation(row: sqlite3.Row) -> Relation:
    return Relation(
        src_task_id=row["src_task_id"],
        dst_task_id=row["dst_task_id"],
        relation_type=row["relation_type"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
    )


class RelationCycleError(Exception):
    """添加/编辑关系会形成同 relation_type 有向环(DFS per-type 隔离检测到)。"""


class RelationNotFoundError(Exception):
    """edit_relation 目标 relation(src→dst)不存在。"""


class RelationStore:
    """task_relations 邻接表 CRUD(共享 tasks.db)。

    与 TaskStore 同库、同 WAL 模式;本表 FK 不强制(KTD9 —— 允许 dangling audit
    记录)。事务模式镜像 TaskStore:每方法 short-lived connection + BEGIN IMMEDIATE。
    """

    DEFAULT_DB_PATH = "~/.ascend_op_agent/tasks.db"

    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
        finally:
            c.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            # journal_mode=WAL 是 db 级持久(TaskStore._init_schema 已设过同库);
            # 此处幂等再设一次,防 RelationStore 先于 TaskStore 初始化的场景。
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(_RELATIONS_SCHEMA)
            c.commit()

    # ---- cycle detection(per relation_type) ----

    def _would_create_cycle(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: str,
        exclude_edge: Optional[Tuple[str, str]] = None,
    ) -> bool:
        """检查 src→dst 加入 relation_type 的 DAG 后是否产生环。

        per relation_type 隔离:只读同 type 边构建邻接表,跨 type 边不算。
        cycle 成立 ⟺ dst 经同 type 边能到达 src(含 src==dst 自环)。

        Args:
            exclude_edge: 可选 (src, dst) 对 —— 读边时跳过(用于 edit_relation:
                被编辑的边无论现属何 type,在"目标 type 图"中按"该边不存在"检测,
                避免同边自我误判为环)。
        """
        if src_task_id == dst_task_id:
            return True  # 自环
        with self._conn() as c:
            rows = c.execute(
                "SELECT src_task_id, dst_task_id FROM task_relations WHERE relation_type = ?",
                (relation_type,),
            ).fetchall()
        adj: dict[str, list[str]] = {}
        for r in rows:
            s, d = r["src_task_id"], r["dst_task_id"]
            if exclude_edge is not None and (s, d) == exclude_edge:
                continue
            adj.setdefault(s, []).append(d)
        # DFS 自 dst:能否到达 src?
        visited: set[str] = set()
        stack = [dst_task_id]
        while stack:
            node = stack.pop()
            if node == src_task_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adj.get(node, []))
        return False

    # ---- CRUD ----

    def add_relation(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: str,
        confidence: Optional[float] = None,
        created_by: str = CREATED_BY_MANUAL,
    ) -> Relation:
        """加边(R2)。幂等:同 (src, dst, type) 已存在则返回既有行,不重复插。

        Raises:
            ValueError: relation_type 不在 RELATION_TYPES。
            RelationCycleError: 加入此边会在该 type DAG 中成环。
        """
        if relation_type not in RELATION_TYPES:
            raise ValueError(
                f"unknown relation type: {relation_type}; expected one of {RELATION_TYPES}"
            )
        existing = self.get_relation(src_task_id, dst_task_id, relation_type)
        if existing is not None:
            return existing  # 幂等
        if self._would_create_cycle(src_task_id, dst_task_id, relation_type):
            raise RelationCycleError(
                f"adding {src_task_id} -[{relation_type}]-> {dst_task_id} would form a "
                f"cycle in the '{relation_type}' DAG"
            )
        now = _now_iso()
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO task_relations"
                    " (src_task_id, dst_task_id, relation_type, confidence,"
                    "  created_at, created_by, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (src_task_id, dst_task_id, relation_type, confidence, now, created_by, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return self.get_relation(src_task_id, dst_task_id, relation_type)  # type: ignore[return-value]

    def edit_relation(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: str,
        confidence: Optional[float] = None,
    ) -> Relation:
        """in-place 改 relation 的 type/confidence(R2 edit,**不删+重插**)。

        保留 created_at/created_by(audit trail),仅 UPDATE relation_type/confidence/
        updated_at。目标 relation 由 (src, dst) 定位(指向同 src→dst 唯一存在的边)。

        Args:
            relation_type: 新 type(目标 type;改 type 时先在该 type DAG 做环检测)。
            confidence: None 则保留既有 confidence。

        Raises:
            ValueError: relation_type 非法 / src→dst 存在多条边(歧义)。
            RelationNotFoundError: src→dst 边不存在。
            RelationCycleError: 改成新 type 后在该 type DAG 成环。
        """
        if relation_type not in RELATION_TYPES:
            raise ValueError(
                f"unknown relation type: {relation_type}; expected one of {RELATION_TYPES}"
            )
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_relations WHERE src_task_id = ? AND dst_task_id = ?",
                (src_task_id, dst_task_id),
            ).fetchall()
        if not rows:
            raise RelationNotFoundError(
                f"no relation from {src_task_id} -> {dst_task_id} to edit"
            )
        if len(rows) > 1:
            raise ValueError(
                f"ambiguous: multiple relation types exist between {src_task_id} and "
                f"{dst_task_id}; unlink + link instead"
            )
        current = _row_to_relation(rows[0])
        # 环检测:目标 type 图中按"该边不存在"评估加入 src→dst 是否成环
        if self._would_create_cycle(
            src_task_id, dst_task_id, relation_type, exclude_edge=(src_task_id, dst_task_id)
        ):
            raise RelationCycleError(
                f"editing {src_task_id} -> {dst_task_id} to '{relation_type}' would form a "
                f"cycle in the '{relation_type}' DAG"
            )
        now = _now_iso()
        new_confidence = confidence if confidence is not None else current.confidence
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "UPDATE task_relations"
                    " SET relation_type = ?, confidence = ?, updated_at = ?"
                    " WHERE src_task_id = ? AND dst_task_id = ?",
                    (relation_type, new_confidence, now, src_task_id, dst_task_id),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        return self.get_relation(src_task_id, dst_task_id, relation_type)  # type: ignore[return-value]

    def remove_relation(
        self,
        src_task_id: str,
        dst_task_id: str,
        relation_type: Optional[str] = None,
    ) -> int:
        """删边(R2 remove)。relation_type=None 删 src→dst 全部 type 边。

        Returns:
            删除行数。
        """
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                if relation_type is not None:
                    cur = c.execute(
                        "DELETE FROM task_relations"
                        " WHERE src_task_id = ? AND dst_task_id = ? AND relation_type = ?",
                        (src_task_id, dst_task_id, relation_type),
                    )
                else:
                    cur = c.execute(
                        "DELETE FROM task_relations WHERE src_task_id = ? AND dst_task_id = ?",
                        (src_task_id, dst_task_id),
                    )
                c.commit()
                return cur.rowcount
            except Exception:
                c.rollback()
                raise

    def get_relation(
        self, src_task_id: str, dst_task_id: str, relation_type: str
    ) -> Optional[Relation]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM task_relations"
                " WHERE src_task_id = ? AND dst_task_id = ? AND relation_type = ?",
                (src_task_id, dst_task_id, relation_type),
            ).fetchone()
        return _row_to_relation(row) if row is not None else None

    def list_relations(self, task_id: str) -> List[Relation]:
        """列与 task_id 相关的全部边(src 或 dst 任一端命中)。按 created_at 排序。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_relations"
                " WHERE src_task_id = ? OR dst_task_id = ?"
                " ORDER BY created_at, src_task_id, dst_task_id",
                (task_id, task_id),
            ).fetchall()
        return [_row_to_relation(r) for r in rows]
