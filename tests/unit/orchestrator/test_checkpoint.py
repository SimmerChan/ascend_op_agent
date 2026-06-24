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
