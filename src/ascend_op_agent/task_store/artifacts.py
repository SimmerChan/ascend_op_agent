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

"""U6 task_artifacts_index —— 受控跨任务读的产物注册表(KTD10)。

读权限(R10)**不**靠直读 src task 的 memory/对话(那是 KTD5 隔离域),而是靠 task
在终态 / 阶段性产出时把产物路径"标"进这张表。下游 task(经 depends-on 边授权,
由 ``context_scope.ContextScope.read_related_artifacts`` 解析)只读这张表拿路径,
**不复制 src context 到执行上下文**(origin R10)。

schema(KTD10):
    ``(task_id, artifact_type, path, written_by, written_at)``
    PK = ``(task_id, path)`` —— 同 task 同 path 幂等 upsert(重写更新 type/by/at)
    索引 = ``(task_id)`` + ``(task_id, artifact_type)``

writer hook(U8 真 executor 集成):
    本 unit 仅 **spec 接口 + 提供 mock writer**(``ArtifactWriter`` callable +
    ``MockArtifactWriter``)。真 executor 在 U8 dispatch boundary 调 ``write(...)``;
    PhaseRunner 集成也在 U8 同期(不在 PhaseRunner 内部)。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Union

logger = logging.getLogger(__name__)

# ---- artifact_type 常用取值(非强制校验,executor 自定义 type 直接写) ----
ARTIFACT_TYPE_REPORT = "report"
ARTIFACT_TYPE_SCRIPT = "script"
ARTIFACT_TYPE_LOG = "log"

# written_by 取值
WRITTEN_BY_MANUAL = "manual"
WRITTEN_BY_EXECUTOR = "executor"

_ARTIFACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_artifacts_index (
    task_id       TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path          TEXT NOT NULL,
    written_by    TEXT NOT NULL,
    written_at    TEXT NOT NULL,
    PRIMARY KEY (task_id, path)
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts_index(task_id);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_type ON task_artifacts_index(task_id, artifact_type);
"""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Artifact:
    """task_artifacts_index 一行。"""

    task_id: str
    artifact_type: str
    path: str
    written_by: str
    written_at: str


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        task_id=row["task_id"],
        artifact_type=row["artifact_type"],
        path=row["path"],
        written_by=row["written_by"],
        written_at=row["written_at"],
    )


class ArtifactStore:
    """task_artifacts_index CRUD(共享 tasks.db,KTD1 隔离域)。

    与 TaskStore / RelationStore 同库、同 WAL 模式;FK 不强制(允许 dangling audit
    记录,镜像 RelationStore)。事务模式镜像 TaskStore:每方法 short-lived
    connection + BEGIN IMMEDIATE。
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
            # 此处幂等再设一次,防 ArtifactStore 先于 TaskStore 初始化的场景。
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(_ARTIFACTS_SCHEMA)
            c.commit()

    def write(
        self,
        task_id: str,
        artifact_type: str,
        path: str,
        written_by: str = WRITTEN_BY_MANUAL,
    ) -> Artifact:
        """标产物路径(KTD10 writer hook 的落库动作)。

        幂等 upsert:同 ``(task_id, path)`` 已存在则更新 type/by/at(PK 冲突
        ON CONFLICT UPDATE)。返回落库后的 Artifact。

        Args:
            task_id: 产物所属 task。
            artifact_type: 自由字符串(report/script/log 或 executor 自定义)。
            path: 产物路径(绝对或相对;只存路径,不验证文件存在 —— 验证是
                executor 的职责,U6 仅做注册表)。
            written_by: 写入来源(manual / executor / executor name)。
        """
        now = _now_iso()
        with self._conn() as c:
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO task_artifacts_index"
                    " (task_id, artifact_type, path, written_by, written_at)"
                    " VALUES (?, ?, ?, ?, ?)"
                    " ON CONFLICT(task_id, path) DO UPDATE SET"
                    "  artifact_type = excluded.artifact_type,"
                    "  written_by = excluded.written_by,"
                    "  written_at = excluded.written_at",
                    (task_id, artifact_type, path, written_by, now),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        row = self._get_row(task_id, path)
        assert row is not None  # 刚写入,必然存在
        return _row_to_artifact(row)

    def _get_row(self, task_id: str, path: str) -> Optional[sqlite3.Row]:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM task_artifacts_index WHERE task_id = ? AND path = ?",
                (task_id, path),
            ).fetchone()

    def get_artifacts(self, task_id: str) -> List[Artifact]:
        """读 task 标过的全部产物路径(跨任务读用,read-only)。按 written_at 升序。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_artifacts_index WHERE task_id = ?"
                " ORDER BY written_at, path",
                (task_id,),
            ).fetchall()
        return [_row_to_artifact(r) for r in rows]

    def get_artifacts_by_type(
        self, task_id: str, artifact_type: str
    ) -> List[Artifact]:
        """读 task 某类型的产物(如只读 report)。按 written_at 升序。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM task_artifacts_index"
                " WHERE task_id = ? AND artifact_type = ?"
                " ORDER BY written_at, path",
                (task_id, artifact_type),
            ).fetchall()
        return [_row_to_artifact(r) for r in rows]


# ---- writer hook spec(U8 真 executor 集成) ----

#: writer hook callable 契约:``(task_id, artifact_type, path) -> None``。
#: executor 在 dispatch boundary(非 PhaseRunner 内部)产出阶段性产物时调它。
#: U6 只 spec 接口 + 提供 mock;U8 wiring 真 executor。
ArtifactWriter = Callable[[str, str, str], None]


def make_writer(
    store: ArtifactStore, written_by: str = WRITTEN_BY_EXECUTOR
) -> ArtifactWriter:
    """构造一个绑到给定 store 的 writer hook(U8 executor wiring 用)。

    Args:
        store: ArtifactStore。
        written_by: 标记写入来源(默认 "executor";可传 executor name)。

    Returns:
        ``callable(task_id, artifact_type, path) -> None``;写入失败 raise(store.write
        的异常透传,U8 executor 决定如何处理)。
    """

    def _writer(task_id: str, artifact_type: str, path: str) -> None:
        store.write(task_id, artifact_type, path, written_by=written_by)

    return _writer


class MockArtifactWriter:
    """U6 mock writer —— 不写真 db 也不依赖真 executor。

    记录全部 ``write`` 调用供测试断言;可选 forward 到真 store(默认不 forward,
    验证"mock 不影响跨 task 读逻辑"用)。U8 集成时换 ``make_writer(...)`` 即可。
    """

    def __init__(self, forward_store: Optional[ArtifactStore] = None):
        self.calls: List[Artifact] = []
        self.forward_store = forward_store

    def __call__(self, task_id: str, artifact_type: str, path: str) -> None:
        self.calls.append(
            Artifact(
                task_id=task_id,
                artifact_type=artifact_type,
                path=path,
                written_by=WRITTEN_BY_EXECUTOR,
                written_at=_now_iso(),
            )
        )
        if self.forward_store is not None:
            self.forward_store.write(task_id, artifact_type, path)
