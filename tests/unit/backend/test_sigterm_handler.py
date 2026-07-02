"""U4.5: JSONRPCServer SIGTERM handler + 30s heartbeat 单测。

覆盖 (P1 plan F-P1-FEAS-11 round 3 + F23 round 2):
- _heartbeat_loop 30s 间隔推 agent.progress event="alive"
- _graceful_shutdown 推 final frame event="shutting_down" + _running=False + exit 143/130
- 0 active notification 时不抛错(空 idle)
- SIGTERM handler 注册(loop.add_signal_handler 可用时)

不依赖真实 stdio / 真实 LLM / 真实 910B。纯 asyncio mock。
用 stdlib asyncio.run 包装(避免 pytest-asyncio 依赖)。
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_server():
    """构造 JSONRPCServer (绕过真实 stdio)。"""
    from ascend_op_agent.backend.rpc.server import JSONRPCServer
    s = JSONRPCServer()
    s.send_notification = AsyncMock()
    return s


# ---- _heartbeat_loop ----


def test_heartbeat_loop_pushes_alive_event() -> None:
    """30s 间隔推 agent.progress event='alive'。"""
    s = _make_server()
    s._running = True

    async def _runner():
        # patch asyncio.sleep: 第 1 次让 heartbeat 推 1 次, 第 2 次让 _running=False 退出
        call_count = {"n": 0}

        async def _fake_sleep(t):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                s._running = False

        with patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", _fake_sleep):
            await s._heartbeat_loop(interval_sec=30.0)

    asyncio.run(_runner())

    # send_notification 被调过(至少 1 次 alive)
    assert s.send_notification.await_count >= 1
    last_call = s.send_notification.await_args
    method, params = last_call.args
    assert method == "agent.progress"
    assert params["event"] == "alive"
    assert "ts" in params["payload"]


def test_heartbeat_loop_stops_when_running_false() -> None:
    """_running=False 后 heartbeat 退出(不抛 CancelledError 给调用方)。

    heartbeat loop 顺序: sleep → 检查 _running → send_notification。
    让 _fake_sleep 第 1 次返(让 notify 推一次),第 2 次设 _running=False(让退出)。
    """
    s = _make_server()
    s._running = True
    call_count = {"n": 0}

    async def _fake_sleep(t):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            s._running = False

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", _fake_sleep):
            await s._heartbeat_loop(interval_sec=30.0)

    asyncio.run(_runner())
    assert s.send_notification.await_count >= 1  # 第 1 次 sleep 后推过 alive


def test_heartbeat_loop_cancelled_no_raise() -> None:
    """task 被 cancel 时 _heartbeat_loop 不抛 CancelledError 给 caller。"""
    s = _make_server()
    s._running = True

    real_sleep = asyncio.sleep  # 用真实 sleep 避免递归

    async def _runner():
        async def _block_sleep(t):
            await real_sleep(100)

        with patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", _block_sleep):
            task = asyncio.ensure_future(s._heartbeat_loop())
            await real_sleep(0.01)
            task.cancel()
            result = await task  # 应正常返(内部捕获 CancelledError)
            return result

    result = asyncio.run(_runner())
    assert result is None  # 无 raise


# ---- _graceful_shutdown ----


def test_graceful_shutdown_pushes_final_frame_and_exits_143() -> None:
    """SIGTERM → final frame event='shutting_down' + sys.exit(143)。"""
    s = _make_server()
    s._running = True

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.sys.exit") as mock_exit, \
             patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", AsyncMock()):
            await s._graceful_shutdown("SIGTERM")
            return mock_exit

    mock_exit = asyncio.run(_runner())
    assert s.send_notification.await_count >= 1
    last_call = s.send_notification.await_args
    method, params = last_call.args
    assert method == "agent.progress"
    assert params["event"] == "shutting_down"
    assert params["payload"]["reason"] == "SIGTERM"
    assert s._running is False
    mock_exit.assert_called_once_with(143)


def test_graceful_shutdown_sigint_exits_130() -> None:
    """SIGINT → exit 130 (128 + 2)。"""
    s = _make_server()
    s._running = True

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.sys.exit") as mock_exit, \
             patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", AsyncMock()):
            await s._graceful_shutdown("SIGINT")
            return mock_exit

    mock_exit = asyncio.run(_runner())
    mock_exit.assert_called_once_with(130)


def test_graceful_shutdown_idempotent_when_already_stopped() -> None:
    """_running=False 时再调 _graceful_shutdown 不重复推 frame + 不 exit。"""
    s = _make_server()
    s._running = False

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.sys.exit") as mock_exit:
            await s._graceful_shutdown("SIGTERM")
            return mock_exit

    mock_exit = asyncio.run(_runner())
    s.send_notification.assert_not_awaited()
    mock_exit.assert_not_called()


def test_graceful_shutdown_continues_even_if_send_notification_fails() -> None:
    """send_notification 抛异常时 shutdown 仍继续(不阻塞退出)。"""
    s = _make_server()
    s._running = True
    s.send_notification = AsyncMock(side_effect=RuntimeError("stdio closed"))

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.sys.exit") as mock_exit, \
             patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", AsyncMock()):
            await s._graceful_shutdown("SIGTERM")
            return mock_exit

    mock_exit = asyncio.run(_runner())
    assert s._running is False
    mock_exit.assert_called_once_with(143)


# ---- run() 注册 signal handler ----


def test_run_registers_sigterm_handler() -> None:
    """run() 启动时调 loop.add_signal_handler(SIGTERM/SIGINT)。"""
    s = _make_server()
    s._read_input = AsyncMock()

    fake_loop = MagicMock()
    fake_loop.add_signal_handler = MagicMock()

    async def _runner():
        with patch("ascend_op_agent.backend.rpc.server.asyncio.get_running_loop",
                   return_value=fake_loop), \
             patch("ascend_op_agent.backend.rpc.server.asyncio.sleep", AsyncMock()):
            try:
                await asyncio.wait_for(s.run(), timeout=0.2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    asyncio.run(_runner())

    sigs_registered = [call.args[0] for call in fake_loop.add_signal_handler.call_args_list]
    assert signal.SIGTERM in sigs_registered
    assert signal.SIGINT in sigs_registered
