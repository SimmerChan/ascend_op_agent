"""U8: AgentAsyncWrapper.make_phase_callback 单测。

验证 PhaseRunner 阶段事件 → orchestrator.progress 通知的桥接契约:

- 有 NotificationQueue 时,phase_callback 把 (phase, event, error) 转成
  ``orchestrator.progress`` 通知放入队列
- 无 NotificationQueue 时,phase_callback 是 noop(不抛)
- PHASE_EVENT_TO_STAGE 映射正确(started/completed/failed/interrupted)
- error 字段仅在非 None 时包含

mock AIAgent(避开重依赖链)。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ascend_op_agent.backend.rpc.agent_service import (
    AgentAsyncWrapper,
    PHASE_EVENT_TO_STAGE,
)


def _make_wrapper(with_queue: bool = True) -> AgentAsyncWrapper:
    """构造 wrapper,mock 掉 AIAgent 和 send_notification_fn。"""
    mock_agent = MagicMock()
    if with_queue:
        # 真的 send_notification_fn(返回 coroutine)才会创建 NotificationQueue
        async def fake_send(method, params):
            return None
        return AgentAsyncWrapper(mock_agent, send_notification_fn=fake_send)
    return AgentAsyncWrapper(mock_agent)


def test_phase_callback_with_queue_puts_orchestrator_progress() -> None:
    """有 NotificationQueue:phase_callback 放入 orchestrator.progress 事件。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()

    cb("design", "started")
    cb("design", "completed")

    queue = wrapper._notification_queue
    assert queue is not None
    # 队列里应有 2 个事件
    e1 = queue._queue.get_nowait()
    e2 = queue._queue.get_nowait()
    assert e1["type"] == "orchestrator.progress"
    assert e1["params"]["phase"] == "design"
    assert e1["params"]["stage"] == "phase_started"
    assert e2["params"]["stage"] == "phase_completed"


def test_phase_callback_includes_error_on_failed_event() -> None:
    """failed 事件:params 包含 error 字段。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()

    cb("codegen", "failed", error="syntax error L42")

    e = wrapper._notification_queue._queue.get_nowait()
    assert e["params"]["stage"] == "phase_failed"
    assert e["params"]["error"] == "syntax error L42"


def test_phase_callback_omits_error_when_none() -> None:
    """非 failed 事件:error 字段不应出现(避免前端误判)。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()

    cb("design", "started")
    e = wrapper._notification_queue._queue.get_nowait()
    assert "error" not in e["params"]


def test_phase_callback_interrupted_event() -> None:
    """interrupted 事件:HITL 暂停场景。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()

    cb("design", "interrupted")
    e = wrapper._notification_queue._queue.get_nowait()
    assert e["params"]["stage"] == "phase_interrupted"


def test_phase_callback_unknown_event_falls_back() -> None:
    """未知 event:stage 字段为 phase_<event>(降级,不丢消息)。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()

    cb("custom_phase", "weird_event")
    e = wrapper._notification_queue._queue.get_nowait()
    assert e["params"]["stage"] == "phase_weird_event"


def test_phase_callback_noop_without_queue() -> None:
    """无 NotificationQueue:回调是 noop(不抛,不阻塞 PhaseRunner)。"""
    wrapper = _make_wrapper(with_queue=False)
    assert wrapper._notification_queue is None

    cb = wrapper.make_phase_callback()
    # 不应抛
    cb("design", "started")
    cb("design", "failed", error="boom")


def test_phase_callback_signature_matches_phaserunner_contract() -> None:
    """回调签名 (phase, event, error=None) 与 PhaseRunner._emit_phase 一致。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.make_phase_callback()
    # 三种调用形式都应可工作
    cb("p1", "started")
    cb("p1", "completed")
    cb("p1", "failed", "reason")
    # 队列应有 3 个
    assert wrapper._notification_queue._queue.qsize() == 3


def test_phase_event_to_stage_mapping_complete() -> None:
    """4 个核心事件都有 stage 映射。"""
    assert PHASE_EVENT_TO_STAGE["started"] == "phase_started"
    assert PHASE_EVENT_TO_STAGE["completed"] == "phase_completed"
    assert PHASE_EVENT_TO_STAGE["failed"] == "phase_failed"
    assert PHASE_EVENT_TO_STAGE["interrupted"] == "phase_interrupted"
