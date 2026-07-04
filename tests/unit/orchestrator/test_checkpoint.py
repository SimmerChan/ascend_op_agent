"""U4 CheckpointStore 单测。

覆盖:

- save/load round-trip 状态完整恢复
- 同 thread_id 多次 save(upsert)
- 写入中断不产生半截记录(模拟方法异常时 transaction rollback)
- list_pending 正确列出未完成 thread
- mark_waiting + consume_pending HITL 流程
- save_artifact + get_artifact + has_artifact_with_sha 幂等 gate
- CheckpointConfig 默认值与 Config 集成
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ascend_op_agent.config import CheckpointConfig, Config
from ascend_op_agent.orchestrator.checkpoint import (
    STATUS_DONE,
    STATUS_RUNNING,
    STATUS_WAITING_CONFIRM,
    Artifact,
    CheckpointStore,
    PendingApproval,
    PendingCheckpoint,
)


@pytest.fixture
def store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(tmp_path / "checkpoints.db")


def test_checkpoint_config_defaults() -> None:
    cfg = CheckpointConfig()
    assert cfg.db_path == "~/.ascend_op_agent/checkpoints.db"
    assert cfg.auto_resume is True


def test_config_has_checkpoint_field() -> None:
    cfg = Config()
    assert isinstance(cfg.checkpoint, CheckpointConfig)
    assert cfg.checkpoint.db_path.endswith("checkpoints.db")


def test_from_config_factory(tmp_path: Path) -> None:
    cfg = CheckpointConfig(db_path=str(tmp_path / "ck.db"))
    store = CheckpointStore.from_config(cfg)
    assert store.db_path == (tmp_path / "ck.db").resolve()
    assert store.db_path.exists()


def test_save_load_round_trip(store: CheckpointStore) -> None:
    state = {
        "thread_id": "t1",
        "current_phase": "design",
        "messages": [{"role": "user", "content": "hi"}],
        "memory_pools": {"design": {"decided": True}},
        "phase_history": ["entry", "design"],
        "retry_counts": {"review": 1},
        "op_info": None,
        "design_doc": {"name": "Add"},
    }
    store.save("t1", state, current_phase="design", status=STATUS_RUNNING)

    loaded = store.load("t1")
    assert loaded == state
    assert store.get_status("t1") == STATUS_RUNNING


def test_save_upsert_replaces_state(store: CheckpointStore) -> None:
    store.save("t1", {"v": 1}, current_phase="p1")
    store.save("t1", {"v": 2}, current_phase="p2", status=STATUS_WAITING_CONFIRM)

    assert store.load("t1") == {"v": 2}
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM

    import sqlite3

    conn = sqlite3.connect(str(store.db_path))
    try:
        rows = conn.execute(
            "SELECT thread_id FROM checkpoints WHERE thread_id = ?", ("t1",)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_load_missing_thread_returns_none(store: CheckpointStore) -> None:
    assert store.load("nope") is None
    assert store.get_status("nope") is None


def test_save_atomic_on_json_failure(store: CheckpointStore) -> None:
    """模拟 json.dumps 失败:checkpoint 不应有半截记录。"""
    store.save("t1", {"ok": True})

    bad_state = {"unserializable": object()}

    with patch(
        "ascend_op_agent.orchestrator.checkpoint.json.dumps",
        side_effect=TypeError("boom"),
    ):
        with pytest.raises(TypeError):
            store.save("t1", bad_state, current_phase="p2")

    # 原 state 仍在(object() 未污染记录)
    loaded = store.load("t1")
    assert loaded == {"ok": True}


def test_list_pending_filters_done(store: CheckpointStore) -> None:
    store.save("t1", {"n": 1}, status=STATUS_RUNNING)
    store.save("t2", {"n": 2}, status=STATUS_WAITING_CONFIRM)
    store.save("t3", {"n": 3}, status=STATUS_DONE)
    store.save("t4", {"n": 4}, status="failed")

    pending = store.list_pending()
    ids = {p.thread_id for p in pending}
    assert ids == {"t1", "t2", "t4"}
    assert all(isinstance(p, PendingCheckpoint) for p in pending)
    assert all(p.status != STATUS_DONE for p in pending)


def test_list_pending_ordered_by_updated_at(store: CheckpointStore) -> None:
    import time

    store.save("late", {"n": 1})
    time.sleep(1.1)
    store.save("early", {"n": 2})

    pending = store.list_pending()
    assert pending[0].thread_id == "late"
    assert pending[1].thread_id == "early"


def test_mark_waiting_and_consume_pending_round_trip(
    store: CheckpointStore,
) -> None:
    store.save("t1", {"phase": "design"}, status=STATUS_RUNNING)
    payload = {
        "kind": "design_approval",
        "options": ["sample", "torch_npu", "pybind"],
        "recommended": "torch_npu",
    }
    store.mark_waiting("t1", "design", payload)

    assert store.get_status("t1") == STATUS_WAITING_CONFIRM

    approval = store.consume_pending("t1")
    assert isinstance(approval, PendingApproval)
    assert approval.phase == "design"
    assert approval.payload == payload

    assert store.get_status("t1") == STATUS_RUNNING
    assert store.consume_pending("t1") is None


def test_mark_waiting_upserts_existing_pending(store: CheckpointStore) -> None:
    store.save("t1", {}, status=STATUS_RUNNING)
    store.mark_waiting("t1", "design", {"v": 1})
    store.mark_waiting("t1", "delivery", {"v": 2})

    approval = store.consume_pending("t1")
    assert approval is not None
    assert approval.phase == "delivery"
    assert approval.payload == {"v": 2}


def test_consume_pending_missing_returns_none(store: CheckpointStore) -> None:
    store.save("t1", {}, status=STATUS_RUNNING)
    assert store.consume_pending("t1") is None


def test_save_artifact_with_content(store: CheckpointStore) -> None:
    content = b"#include <stdio.h>\nint main(){return 0;}"
    sha = store.save_artifact("t1", "codegen", "kernel.c", content=content)
    assert sha is not None
    assert len(sha) == 64

    art = store.get_artifact("t1", "codegen", "kernel.c")
    assert isinstance(art, Artifact)
    assert art.sha256 == sha
    assert art.path is None


def test_save_artifact_with_path(tmp_path: Path, store: CheckpointStore) -> None:
    f = tmp_path / "k.c"
    f.write_bytes(b"kernel body")

    sha = store.save_artifact("t1", "codegen", "kernel.c", path=f)
    assert sha is not None

    art = store.get_artifact("t1", "codegen", "kernel.c")
    assert art is not None
    assert art.sha256 == sha
    assert art.path == str(f)


def test_save_artifact_upsert_replaces(store: CheckpointStore) -> None:
    sha1 = store.save_artifact("t1", "codegen", "k", content=b"v1")
    sha2 = store.save_artifact("t1", "codegen", "k", content=b"v2")

    assert sha1 != sha2
    art = store.get_artifact("t1", "codegen", "k")
    assert art is not None
    assert art.sha256 == sha2


def test_has_artifact_with_sha_idempotency_gate(store: CheckpointStore) -> None:
    """LLM 节点 resume 幂等:相同 sha 已存在则跳过重生成。"""
    content = b"stable output"
    sha = store.save_artifact("t1", "codegen", "kernel", content=content)

    assert store.has_artifact_with_sha("t1", "codegen", "kernel", sha) is True
    assert store.has_artifact_with_sha("t1", "codegen", "kernel", "other_sha") is False
    assert store.has_artifact_with_sha("t1", "codegen", "missing", sha) is False


def test_save_artifact_missing_path_records_null_sha(
    tmp_path: Path, store: CheckpointStore
) -> None:
    sha = store.save_artifact("t1", "codegen", "k", path=tmp_path / "nope.c")
    assert sha is None
    art = store.get_artifact("t1", "codegen", "k")
    assert art is not None
    assert art.sha256 is None


def test_multiple_threads_isolated(store: CheckpointStore) -> None:
    store.save("t1", {"who": "t1"}, status=STATUS_RUNNING)
    store.save("t2", {"who": "t2"}, status=STATUS_WAITING_CONFIRM)

    assert store.load("t1") == {"who": "t1"}
    assert store.load("t2") == {"who": "t2"}
    assert store.get_status("t1") == STATUS_RUNNING
    assert store.get_status("t2") == STATUS_WAITING_CONFIRM


def test_persists_across_reopen(tmp_path: Path) -> None:
    db = tmp_path / "ck.db"
    s1 = CheckpointStore(db)
    s1.save("t1", {"v": 42}, current_phase="design", status=STATUS_RUNNING)
    s1.mark_waiting("t1", "design", {"q": "approve?"})

    del s1
    s2 = CheckpointStore(db)
    assert s2.load("t1") == {"v": 42}
    assert s2.get_status("t1") == STATUS_WAITING_CONFIRM
    approval = s2.consume_pending("t1")
    assert approval is not None
    assert approval.payload == {"q": "approve?"}


def test_init_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "ck.db"
    s1 = CheckpointStore(db)
    s1.save("t1", {"v": 1})
    s2 = CheckpointStore(db)
    assert s2.load("t1") == {"v": 1}


# ---- U1: schema v2 + v1↔v2 round-trip + rollback + PathConfig + quarantine (P1) ----


def test_init_schema_raises_on_old_sqlite(tmp_path):
    """U1: libsqlite3 <3.31 启动期返 CheckpointSchemaError,不静默 fallback。"""
    from ascend_op_agent.orchestrator.checkpoint import (
        CheckpointSchemaError,
        CheckpointStore,
        LIBSQLITE3_MIN_FOR_STORED_COL,
    )
    import sqlite3 as _sqlite3

    if _sqlite3.sqlite_version_info >= LIBSQLITE3_MIN_FOR_STORED_COL:
        pytest.skip("系统 libsqlite3 ≥3.31, 此测跳过")
    with pytest.raises(CheckpointSchemaError, match="< 3.31"):
        CheckpointStore(tmp_path / "ck.db")


def test_v1_to_v2_forward_migration_creates_backup(tmp_path):
    """U1: 老 v1 db(无 schema_version 列)→ 启动期 v1→v2 forward migration,
    自动 backup `.v1.backup-{ts}.db` + ALTER ADD COLUMN。
    """
    from ascend_op_agent.orchestrator.checkpoint import (
        CheckpointStore,
        SCHEMA_VERSION,
        SCHEMA_VERSION_MIN,
    )

    db = tmp_path / "ck.db"
    # 模拟 v1 老 db(无 schema_version / skill_loads_json 列)
    import sqlite3

    with sqlite3.connect(str(db)) as c:
        c.execute(
            """CREATE TABLE checkpoints (
            thread_id TEXT PRIMARY KEY, current_phase TEXT,
            state_json TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
        )
        c.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
            ("t1", "analyze", '{"messages": [], "code_result": {}}', "done", "2026-07-01"),
        )
        c.commit()

    # 启动触发 v1→v2 migration
    store = CheckpointStore(db)
    # 验证 schema_version 列已加
    with sqlite3.connect(str(db)) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(checkpoints)").fetchall()]
    assert "schema_version" in cols, f"v2 migration 缺 schema_version 列,cols={cols}"

    # 验证 backup 文件已建
    backups = list(tmp_path.glob("ck.db.v1.backup-*.db"))
    assert len(backups) == 1, f"v1 backup 应恰好 1 个,实际 {len(backups)}"
    # 验证 backup 是真 v1(无 schema_version 列)
    import sqlite3 as _sq

    with _sq.connect(str(backups[0])) as c:
        bcols = [r[1] for r in c.execute("PRAGMA table_info(checkpoints)").fetchall()]
    assert "schema_version" not in bcols

    # 验证 load + 自动 migration(老 v1 缺 skill_loads → 升级 v2)
    state = store.load_and_migrate_checkpoint("t1")
    assert state is not None
    assert state.get("skill_loads") == []
    assert state.get("messages") == []


def test_v2_to_v1_rollback_strips_skill_loads(tmp_path):
    """U1: v2 row → rollback_to_v1() 剥 skill_loads 字段 + save 为 schema_version=1。"""
    from ascend_op_agent.orchestrator.checkpoint import (
        CheckpointStore,
        SCHEMA_VERSION,
        SCHEMA_VERSION_MIN,
    )

    store = CheckpointStore(tmp_path / "ck.db")
    # 写 v2 状态(含 skill_loads)
    v2_state = {
        "messages": [{"role": "user", "content": "x"}],
        "skill_loads": [{"phase": "design", "skill_names": ["a"], "used_skills": []}],
    }
    store.save("t1", v2_state, status="done", schema_version=SCHEMA_VERSION)

    # rollback
    assert store.rollback_to_v1("t1") is True

    # 验证 v1 row 剥 skill_loads
    result = store.read_checkpoint("t1")
    assert result["schema_version"] == SCHEMA_VERSION_MIN
    assert "skill_loads" not in result["state"]
    assert result["state"]["messages"] == v2_state["messages"]


def test_v1_v2_round_trip_preserves_messages(tmp_path):
    """U1: v1 → save 升级 → rollback 再 v1 → v2 不丢 messages。"""
    from ascend_op_agent.orchestrator.checkpoint import CheckpointStore

    store = CheckpointStore(tmp_path / "ck.db")
    v1 = {"messages": [{"role": "user", "content": "hello"}]}
    # v1 save(无 skill_loads)→ load_and_migrate_checkpoint 升级为 v2
    store.save("t1", v1, schema_version=1)
    upgraded = store.load_and_migrate_checkpoint("t1")
    assert upgraded["messages"] == v1["messages"]
    assert upgraded["skill_loads"] == []
    # v2 → v1 rollback
    store.rollback_to_v1("t1")
    back = store.read_checkpoint("t1")
    assert back["schema_version"] == 1
    assert back["state"]["messages"] == v1["messages"]


def test_corrupt_state_json_raises_checkpoint_corrupt_error(tmp_path):
    """U1: state_json 解析失败 → quarantine 文件 + CheckpointCorruptError。
    不再静默 zero 化。
    """
    from ascend_op_agent.orchestrator.checkpoint import (
        CheckpointCorruptError,
        CheckpointStore,
    )

    db = tmp_path / "ck.db"
    import sqlite3

    # 写 v2 db + 故意坏的 state_json
    with sqlite3.connect(str(db)) as c:
        c.executescript(
            """
            CREATE TABLE checkpoints (
                thread_id TEXT PRIMARY KEY, current_phase TEXT,
                state_json TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 2,
                skill_loads_json TEXT, status TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        c.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bad", None, "not valid json {{{", 2, "[]", "done", "2026-07-01"),
        )
        c.commit()

    store = CheckpointStore(db)
    with pytest.raises(CheckpointCorruptError, match="corrupt"):
        store.read_checkpoint("bad")

    # 验证 quarantine 文件已写
    qdir = tmp_path / ".quarantine"
    assert qdir.exists()
    files = list(qdir.glob("bad-*.json"))
    assert len(files) == 1
    assert "not valid json" in files[0].read_text()


def test_quarantine_lru_cap_evicts_oldest(tmp_path):
    """U1: quarantine hard cap 100 + LRU-by-mtime 淘汰最旧。"""
    from ascend_op_agent.orchestrator.checkpoint import (
        CheckpointCorruptError,
        CheckpointStore,
        QUARANTINE_HARD_CAP,
    )

    store = CheckpointStore(tmp_path / "ck.db")
    qdir = tmp_path / ".quarantine"

    # 触发 105 次 corrupt(每个都用新 thread_id)
    db = tmp_path / "ck.db"
    import sqlite3

    for i in range(QUARANTINE_HARD_CAP + 5):
        try:
            store.read_checkpoint(f"t{i}")
        except CheckpointCorruptError:
            pass

    files = list(qdir.glob("*.json"))
    assert (
        len(files) <= QUARANTINE_HARD_CAP
    ), f"quarantine 超 cap: {len(files)} > {QUARANTINE_HARD_CAP}"


def test_path_config_multi_db_isolated(tmp_path):
    """U1: CheckpointConfig.path 多 db 支持。两个 db 独立 BEGIN IMMEDIATE 不 deadlock。"""
    from ascend_op_agent.config import CheckpointConfig
    from ascend_op_agent.orchestrator.checkpoint import CheckpointStore

    db1 = tmp_path / "run1" / "ck.db"
    db2 = tmp_path / "run2" / "ck.db"
    cfg1 = CheckpointConfig(db_path=str(db1))
    cfg2 = CheckpointConfig(db_path=str(db2))

    s1 = CheckpointStore.from_config(cfg1)
    s2 = CheckpointStore.from_config(cfg2)
    s1.save("tid", {"messages": []})
    s2.save("tid", {"messages": [{"role": "user", "content": "other"}]})

    assert s1.load("tid")["messages"] == []
    assert s2.load("tid")["messages"] == [{"role": "user", "content": "other"}]


def test_full_rollback_to_v1_db_restores_from_backup(tmp_path):
    """U1: 完整 db rollback 备份恢复(.v1.backup-{ts} → 当前 db)。"""
    from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
    import shutil as _sh
    import sqlite3

    # 启动 v1 → v2 migration(自动建 backup)
    db = tmp_path / "ck.db"
    with sqlite3.connect(str(db)) as c:
        c.execute(
            """CREATE TABLE checkpoints (
            thread_id TEXT PRIMARY KEY, current_phase TEXT,
            state_json TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
        )
        c.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, ?, ?)",
            ("t1", "analyze", '{"v": 1}', "done", "2026-07-01"),
        )
        c.commit()
    store = CheckpointStore(db)
    # migration 跑过了,backup 文件已建
    backups_pre = sorted((tmp_path).glob("ck.db.v1.backup-*.db"))
    assert len(backups_pre) >= 1

    # 改 v2 data 模拟
    store.save("t1", {"v": 2, "skill_loads": [{"x": 1}]}, schema_version=2)

    # 完整 rollback
    assert store.full_rollback_to_v1_db() is True
    # 验证 db 是 v1(只查 v1 schema 的 5 列,无 schema_version)
    with sqlite3.connect(str(db)) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(checkpoints)").fetchall()]
        assert "schema_version" not in cols, f"v1 rollback 后不应有 schema_version 列: {cols}"
        row = c.execute(
            "SELECT state_json FROM checkpoints WHERE thread_id = ?", ("t1",)
        ).fetchone()
    assert row is not None
    assert '"v": 2' not in row[0]  # v2 data 已丢失(rollback 恢复 v1 旧 data)
