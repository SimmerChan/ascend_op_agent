"""U7: backend 接线 + resume 检测 测试。

覆盖 Fallback 路径(不破坏现有 agent.run):

- ``session.list_pending`` RPC handler:列出 pending checkpoint
- ``session.resume_with_input`` RPC handler:
  - 无 orchestrator → 返回 error(U7 阶段 fallback)
  - 有 orchestrator → 调用 ``orchestrator.resume`` 并返回状态
- ``_resume_pending_check``:启动时检测 pending 打日志
- ``_setup_agent`` 初始化 ``CheckpointStore``

mock 替代真实 LLM/orchestrator,避开重依赖。

backend.py 是顶层模块(与 backend/ 包同名),通过 ``importlib.util`` 加载。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _load_backend_module():
    """加载 backend.py 顶层模块(避开 backend/ 包)。"""
    backend_py = Path(__file__).parent.parent.parent / "src" / "ascend_op_agent" / "backend.py"
    spec = importlib.util.spec_from_file_location(
        "ascend_op_agent_backend_module_under_test", backend_py
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def backend_module(monkeypatch):
    """加载 backend.py 模块,每测试独立(module-level global 隔离)。"""
    mod = _load_backend_module()
    # 重置 module-level globals
    monkeypatch.setattr(mod, "_checkpoint_store", None, raising=False)
    monkeypatch.setattr(mod, "_orchestrator", None, raising=False)
    return mod


# ---- session.list_pending ----


def test_list_pending_returns_empty_when_store_uninitialized(backend_module) -> None:
    """未初始化 CheckpointStore:返回 error。"""
    result = asyncio.run(backend_module._handle_session_list_pending())
    assert result["status"] == "error"
    assert "not initialized" in result["message"]


def test_list_pending_returns_pending_list(backend_module, tmp_path) -> None:
    """有 pending checkpoint:返回列表。"""
    from ascend_op_agent.orchestrator import CheckpointStore, STATUS_RUNNING

    store = CheckpointStore(tmp_path / "ck.db")
    store.save("t1", {"thread_id": "t1"}, current_phase="design", status=STATUS_RUNNING)
    store.save("t2", {"thread_id": "t2"}, current_phase="codegen", status=STATUS_RUNNING)
    backend_module._checkpoint_store = store

    result = asyncio.run(backend_module._handle_session_list_pending())
    assert result["status"] == "success"
    assert result["count"] == 2
    thread_ids = {p["thread_id"] for p in result["pending"]}
    assert thread_ids == {"t1", "t2"}
    # 元数据齐全
    p0 = result["pending"][0]
    assert "current_phase" in p0
    assert "status" in p0
    assert "updated_at" in p0


def test_list_pending_excludes_done(backend_module, tmp_path) -> None:
    """done 状态的 checkpoint 不在 pending 列表。"""
    from ascend_op_agent.orchestrator import CheckpointStore, STATUS_DONE, STATUS_RUNNING

    store = CheckpointStore(tmp_path / "ck.db")
    store.save("t_done", {"thread_id": "t_done"}, current_phase="done", status=STATUS_DONE)
    store.save("t_running", {"thread_id": "t_running"}, current_phase="x", status=STATUS_RUNNING)
    backend_module._checkpoint_store = store

    result = asyncio.run(backend_module._handle_session_list_pending())
    assert result["count"] == 1
    assert result["pending"][0]["thread_id"] == "t_running"


# ---- session.resume_with_input ----


def test_resume_returns_error_when_no_orchestrator(backend_module) -> None:
    """U7 fallback:orchestrator 未注入 → 友好 error。"""
    result = asyncio.run(
        backend_module._handle_session_resume_with_input(thread_id="t1", payload={"approved": True})
    )
    assert result["status"] == "error"
    assert "not wired" in result["data"]["message"]


def test_resume_calls_orchestrator_resume_and_returns_state(backend_module) -> None:
    """orchestrator 已注入:调用 resume 并把 state 字段透传到 data。"""
    fake_state = {
        "thread_id": "t1",
        "current_phase": "codegen",
        "pending_confirmation": None,
        "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
    }
    mock_orch = MagicMock()
    mock_orch.resume.return_value = fake_state
    backend_module._orchestrator = mock_orch

    result = asyncio.run(
        backend_module._handle_session_resume_with_input(thread_id="t1", payload={"approved": True})
    )

    mock_orch.resume.assert_called_once_with("t1", payload={"approved": True})
    assert result["status"] == "completed"
    assert result["data"]["current_phase"] == "codegen"
    assert result["data"]["messages_count"] == 2


def test_resume_returns_interrupted_when_pending_set(backend_module) -> None:
    """resume 后 state 仍有 pending_confirmation → status=interrupted。"""
    fake_state = {
        "thread_id": "t1",
        "current_phase": "design",
        "pending_confirmation": {"phase": "design", "options": ["yes", "no"]},
        "messages": [],
    }
    mock_orch = MagicMock()
    mock_orch.resume.return_value = fake_state
    backend_module._orchestrator = mock_orch

    result = asyncio.run(backend_module._handle_session_resume_with_input(thread_id="t1"))
    assert result["status"] == "interrupted"
    assert result["data"]["pending_confirmation"]["options"] == ["yes", "no"]


def test_resume_propagates_orchestrator_exception_as_error(backend_module) -> None:
    """orchestrator.resume 抛异常 → 返回 error 而非 crash 整个 RPC。"""
    mock_orch = MagicMock()
    mock_orch.resume.side_effect = ValueError("no checkpoint for thread ghost")
    backend_module._orchestrator = mock_orch

    result = asyncio.run(backend_module._handle_session_resume_with_input(thread_id="ghost"))
    assert result["status"] == "error"
    assert "no checkpoint" in result["data"]["message"]
    assert result["data"]["thread_id"] == "ghost"


# ---- _resume_pending_check ----


def test_resume_pending_check_noop_when_store_none(backend_module, caplog) -> None:
    """无 CheckpointStore:静默返回(不抛)。"""
    backend_module._checkpoint_store = None
    with caplog.at_level(logging.INFO):
        backend_module._resume_pending_check(auto_resume=True)
    # 没崩即过
    assert True


def test_resume_pending_check_logs_pending_threads(backend_module, tmp_path, caplog) -> None:
    """有 pending:日志记录 thread_id + status + phase。"""
    from ascend_op_agent.orchestrator import CheckpointStore, STATUS_RUNNING

    store = CheckpointStore(tmp_path / "ck.db")
    store.save("t1", {"thread_id": "t1"}, current_phase="design", status=STATUS_RUNNING)
    backend_module._checkpoint_store = store

    with caplog.at_level(logging.WARNING):
        backend_module._resume_pending_check(auto_resume=False)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "Found 1 pending checkpoint" in joined
    assert "t1" in joined
    assert "design" in joined


def test_resume_pending_check_auto_resume_logs_hint(backend_module, tmp_path, caplog) -> None:
    """auto_resume=True:日志给出 'use op.resume RPC' 提示。"""
    from ascend_op_agent.orchestrator import CheckpointStore, STATUS_RUNNING

    store = CheckpointStore(tmp_path / "ck.db")
    store.save("t1", {"thread_id": "t1"}, current_phase="x", status=STATUS_RUNNING)
    backend_module._checkpoint_store = store

    with caplog.at_level(logging.INFO):
        backend_module._resume_pending_check(auto_resume=True)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "op.resume" in joined
    assert "P0 does not auto-invoke" in joined


def test_resume_pending_check_no_pending_logs_clean(backend_module, tmp_path, caplog) -> None:
    """无 pending:日志 'No pending checkpoints found'。"""
    from ascend_op_agent.orchestrator import CheckpointStore

    store = CheckpointStore(tmp_path / "ck.db")
    backend_module._checkpoint_store = store

    with caplog.at_level(logging.INFO):
        backend_module._resume_pending_check(auto_resume=True)

    joined = "\n".join(rec.message for rec in caplog.records)
    assert "No pending checkpoints" in joined


# ---- _setup_agent 初始化 CheckpointStore(集成) ----


def test_setup_agent_initializes_checkpoint_store(tmp_path, monkeypatch) -> None:
    """_setup_agent 应实例化 CheckpointStore 到 _checkpoint_store 全局。

    本测试在 Python 3.11 环境下完整运行;py39 环境因 sentence_transformers/
    sklearn/pandas 二进制不匹配,在 ``_setup_agent`` 内部触发
    ``from ascend_op_agent.agent.core import AIAgent`` 时会崩,因此跳过。
    真实环境跑 py311 即可验证。
    """
    if sys.version_info < (3, 11):
        pytest.skip("_setup_agent 触发 sentence_transformers 重依赖,仅 py311 环境验证")

    mod = _load_backend_module()
    fake_server = MagicMock()
    monkeypatch.setattr(mod, "_server", fake_server)
    monkeypatch.setattr(mod, "_checkpoint_store", None, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    mod._setup_agent(config_path=None)
    assert mod._checkpoint_store is not None
    assert mod._checkpoint_store.db_path.exists()


# ---- U6: op: 前缀路由 + Orchestrator wire ----


def test_op_prefix_routes_to_orchestrator_when_wired(backend_module) -> None:
    """U6: agent.run RPC 带 ``op:`` 前缀 → 调 orchestrator.invoke,不走 AIAgent fallback。"""
    # use backend_module._handle_run_conversation(已注入 fixture)

    saved = backend_module._orchestrator
    mock_orch = MagicMock()
    mock_orch.invoke.return_value = {
        "current_phase": "done",
        "pending_confirmation": None,
        "messages": [],
    }
    backend_module._orchestrator = mock_orch
    backend_module._agent_wrapper = MagicMock()  # avoid Agent not initialized bail
    try:
        resp = asyncio.run(backend_module._handle_run_conversation("op: 实现 add 算子"))
        assert resp["status"] == "completed"
        assert resp["data"]["current_phase"] == "done"
        assert resp["data"]["thread_id"]
        mock_orch.invoke.assert_called_once()
        args, kwargs = mock_orch.invoke.call_args
        assert args[0] == "op: 实现 add 算子"
        assert "thread_id" in kwargs
    finally:
        backend_module._orchestrator = saved


def test_op_prefix_interrupted_when_pending_confirmation(backend_module) -> None:
    """U6: orchestrator 中断(HITL)→ status=interrupted + pending_confirmation 透传。"""
    # use backend_module._handle_run_conversation(已注入 fixture)

    saved = backend_module._orchestrator
    mock_orch = MagicMock()
    mock_orch.invoke.return_value = {
        "current_phase": "design",
        "pending_confirmation": {"phase": "design", "options": ["approve", "reject"]},
        "messages": [{"role": "assistant", "content": "请确认"}],
    }
    backend_module._orchestrator = mock_orch
    backend_module._agent_wrapper = MagicMock()  # avoid Agent not initialized bail
    try:
        resp = asyncio.run(backend_module._handle_run_conversation("op: 实现 add 算子"))
        assert resp["status"] == "interrupted"
        assert resp["data"]["current_phase"] == "design"
        assert resp["data"]["pending_confirmation"]["phase"] == "design"
        assert resp["data"]["messages_count"] == 1
    finally:
        backend_module._orchestrator = saved


def test_op_prefix_falls_back_when_orchestrator_none(backend_module) -> None:
    """U6: orchestrator 不可用(None)→ fallback 老 AIAgent path(不抛)。"""
    # use backend_module._handle_run_conversation(已注入 fixture)

    saved_orch = backend_module._orchestrator
    backend_module._orchestrator = None
    saved_wrapper = backend_module._agent_wrapper
    mock_wrapper = MagicMock()
    from unittest.mock import AsyncMock

    mock_wrapper.run_conversation_async = AsyncMock(
        return_value={"status": "completed", "response": "fallback path", "data": {}}
    )
    backend_module._agent_wrapper = mock_wrapper
    try:
        resp = asyncio.run(backend_module._handle_run_conversation("op: 实现 add 算子"))
        assert resp["status"] == "completed"
        mock_wrapper.run_conversation_async.assert_called_once_with("op: 实现 add 算子")
    finally:
        backend_module._orchestrator = saved_orch
        backend_module._agent_wrapper = saved_wrapper


def test_non_op_prefix_skips_orchestrator(backend_module) -> None:
    """U6: 老 TUI 输入(无 op: 前缀)→ 走老 AIAgent path,orchestrator 不调。"""
    # use backend_module._handle_run_conversation(已注入 fixture)

    saved_orch = backend_module._orchestrator
    mock_orch = MagicMock()
    backend_module._orchestrator = mock_orch
    saved_wrapper = backend_module._agent_wrapper
    mock_wrapper = MagicMock()
    from unittest.mock import AsyncMock

    mock_wrapper.run_conversation_async = AsyncMock(
        return_value={"status": "completed", "response": "legacy", "data": {}}
    )
    backend_module._agent_wrapper = mock_wrapper
    try:
        resp = asyncio.run(backend_module._handle_run_conversation("帮我查一下 CANN 文档"))
        assert resp["status"] == "completed"
        mock_wrapper.run_conversation_async.assert_called_once()
        mock_orch.invoke.assert_not_called()
    finally:
        backend_module._orchestrator = saved_orch
        backend_module._agent_wrapper = saved_wrapper


def test_op_prefix_orchestrator_exception_returns_error(backend_module) -> None:
    """U6: orchestrator.invoke 抛异常 → status=error,不 crash。"""
    # use backend_module._handle_run_conversation(已注入 fixture)

    saved = backend_module._orchestrator
    mock_orch = MagicMock()
    mock_orch.invoke.side_effect = RuntimeError("compile fail boom")
    backend_module._orchestrator = mock_orch
    backend_module._agent_wrapper = MagicMock()  # avoid Agent not initialized bail
    try:
        resp = asyncio.run(backend_module._handle_run_conversation("op: 实现 add 算子"))
        assert resp["status"] == "error"
        assert "compile fail boom" in resp["data"]["message"]
        assert resp["data"]["thread_id"]
    finally:
        backend_module._orchestrator = saved


def test_session_resume_real_orchestrator_when_wired(backend_module) -> None:
    """U6: session.resume_with_input → 真调 orchestrator.resume(无 None 短路)。"""
    # use backend_module._handle_session_resume_with_input(已注入 fixture)

    saved = backend_module._orchestrator
    mock_orch = MagicMock()
    mock_orch.resume.return_value = {
        "current_phase": "compile",
        "pending_confirmation": None,
        "messages": [],
    }
    backend_module._orchestrator = mock_orch
    try:
        resp = asyncio.run(
            backend_module._handle_session_resume_with_input(
                "thread-abc", payload={"approved": True}
            )
        )
        assert resp["status"] == "completed"
        mock_orch.resume.assert_called_once_with("thread-abc", payload={"approved": True})
    finally:
        backend_module._orchestrator = saved
