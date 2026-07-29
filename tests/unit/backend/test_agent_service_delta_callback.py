"""U5: AgentAsyncWrapper stream_delta_callback + _DeltaBatcher 单测。

验证 stream text delta → agent.progress{stage:thinking, delta} 桥接:
- wrapper 注入 agent._stream_delta_callback
- _DeltaBatcher push 节流(interval 内累积,不 per-chunk 发)
- finish() / interval 到 -> flush 合并 buffer 为单个 agent.progress
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ascend_op_agent.backend.rpc.agent_service import (
    AgentAsyncWrapper,
    _DeltaBatcher,
)


def _make_wrapper(with_queue: bool = True) -> AgentAsyncWrapper:
    mock_agent = MagicMock()
    if with_queue:

        async def fake_send(method, params):
            return None

        return AgentAsyncWrapper(mock_agent, send_notification_fn=fake_send)
    return AgentAsyncWrapper(mock_agent)


def test_wrapper_injects_stream_delta_callback() -> None:
    """有 queue -> wrapper 注入 agent._stream_delta_callback(非 None,run_conversation 透传 on_delta)。"""
    wrapper = _make_wrapper(with_queue=True)
    assert wrapper.agent._stream_delta_callback is not None


def test_delta_batcher_throttles_within_interval() -> None:
    """interval 内多次 push 不立即发(累积,防 per-token stdout 洪水)。"""
    wrapper = _make_wrapper(with_queue=True)
    batcher = _DeltaBatcher(wrapper._notification_queue, interval_s=99.0)
    batcher.push("a")
    batcher.push("b")
    batcher.push("c")
    assert wrapper._notification_queue._queue.qsize() == 0


def test_delta_batcher_finish_merges_buffer() -> None:
    """finish() flush 累积 buffer -> 单个 agent.progress{stage:thinking, delta} 合并。"""
    wrapper = _make_wrapper(with_queue=True)
    batcher = _DeltaBatcher(wrapper._notification_queue, interval_s=99.0)
    batcher.push("hello ")
    batcher.push("world")
    assert wrapper._notification_queue._queue.qsize() == 0
    batcher.finish()
    e = wrapper._notification_queue._queue.get_nowait()
    assert e["type"] == "agent.progress"
    assert e["params"]["stage"] == "thinking"
    assert e["params"]["delta"] == "hello world"


def test_delta_batcher_flush_on_interval_elapsed() -> None:
    """interval 到 -> 自动 flush。"""
    wrapper = _make_wrapper(with_queue=True)
    batcher = _DeltaBatcher(wrapper._notification_queue, interval_s=0.0)
    batcher.push("x")
    e = wrapper._notification_queue._queue.get_nowait()
    assert e["params"]["delta"] == "x"


def test_delta_batcher_finish_empty_noop() -> None:
    """空 buffer finish -> noop(不发空 delta)。"""
    wrapper = _make_wrapper(with_queue=True)
    batcher = _DeltaBatcher(wrapper._notification_queue, interval_s=99.0)
    batcher.finish()
    assert wrapper._notification_queue._queue.qsize() == 0


def test_stream_delta_callback_routes_to_queue() -> None:
    """wrapper._stream_delta_callback push + finish -> queue 收到合并 delta(端到端桥接)。"""
    wrapper = _make_wrapper(with_queue=True)
    cb = wrapper.agent._stream_delta_callback
    cb("chunk1")
    cb("chunk2")
    # 直接拿 wrapper 的 batcher finish(模拟 status_callback idle 收尾)
    # wrapper 内 batcher 是闭包变量,这里通过 status_callback("idle") 触发 finish
    wrapper.agent._status_callback("idle")
    events = []
    while not wrapper._notification_queue._queue.empty():
        events.append(wrapper._notification_queue._queue.get_nowait())
    delta_events = [
        e for e in events if e["params"].get("stage") == "thinking" and "delta" in e["params"]
    ]
    # 不论 flush 次数(interval 时序),总 delta 含 chunk1 + chunk2
    all_delta = "".join(e["params"]["delta"] for e in delta_events)
    assert "chunk1" in all_delta
    assert "chunk2" in all_delta
