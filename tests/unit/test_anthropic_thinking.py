"""AnthropicAdapter streaming + per-provider max_tokens + on_delta 单测。

U3(2026-07-29):complete 改 streaming(messages.stream + get_final_message),
per-provider max_tokens 自适应(_resolve_limit 按 api_base + min),on_delta 转发
visible text delta。保留 disable_thinking(禁推理)。
OQ2 实证三端点 tool_use 聚合完整。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ascend_op_agent.agent.providers.anthropic_adapter import AnthropicAdapter
from ascend_op_agent.agent.providers.base import ToolCallResult


def _cfg(
    disable_thinking: bool = False,
    max_tokens=None,
    api_base: str = "http://x",
) -> SimpleNamespace:
    return SimpleNamespace(
        api_key="k",
        auth_token="",
        api_base=api_base,
        model="m",
        max_retries=1,
        timeout=10,
        disable_thinking=disable_thinking,
        max_tokens=max_tokens,
    )


def _fake_text_resp(text: str = "hi"):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason="end_turn")


def _fake_tool_use_resp():
    block = SimpleNamespace(type="tool_use", id="t1", name="get_weather", input={"city": "X"})
    return SimpleNamespace(content=[block], stop_reason="tool_use")


def _set_stream_resp(client, resp, text_chunks=None):
    """Mock client.messages.stream() context manager:get_final_message 返 resp;
    text_stream 返 text_chunks 迭代器(on_delta 转发用)。"""
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = resp
    cm.__enter__.return_value.text_stream = iter(text_chunks or [])
    client.messages.stream.return_value = cm


def _make_adapter(disable_thinking: bool = False, **cfg_kw) -> AnthropicAdapter:
    ad = AnthropicAdapter(_cfg(disable_thinking=disable_thinking, **cfg_kw))
    ad._client = MagicMock()
    _set_stream_resp(ad._client, _fake_text_resp())
    return ad


# ---- disable_thinking(保留)----


def test_disable_thinking_true_passes_disabled_param():
    """disable_thinking=True -> stream kwargs 含 thinking={type:disabled}。"""
    ad = _make_adapter(True)
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    _, kwargs = ad._client.messages.stream.call_args
    assert kwargs.get("thinking") == {"type": "disabled"}


def test_disable_thinking_false_omits_param():
    """disable_thinking=False -> 不传 thinking(保护 Minimax 等兼容端点)。"""
    ad = _make_adapter(False)
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    _, kwargs = ad._client.messages.stream.call_args
    assert "thinking" not in kwargs


def test_disable_thinking_returns_text_not_empty():
    """回归:禁 thinking 后 adapter 正常返回 text block 内容(非空)。"""
    ad = _make_adapter(True)
    _set_stream_resp(ad._client, _fake_text_resp("```cpp\nCODE\n```"))
    resp = ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    assert resp == "```cpp\nCODE\n```"


# ---- max_tokens 默认 + per-provider 自适应(OQ10:default None 用 provider 上限)----


def test_max_tokens_default_none():
    """adapter max_tokens 默认 None(用 provider 真实上限,不再硬编码 16384)。"""
    ad = AnthropicAdapter(_cfg())
    assert ad.max_tokens is None


def test_max_tokens_override_from_config():
    """config 设 max_tokens -> adapter 用配置值。"""
    ad = AnthropicAdapter(_cfg(max_tokens=8192))
    assert ad.max_tokens == 8192


def test_max_tokens_none_uses_provider_limit():
    """max_tokens=None + GLM 官方 api_base -> stream kwargs max_tokens=131072(用满)。"""
    ad = _make_adapter(api_base="https://open.bigmodel.cn/api/anthropic")
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    _, kwargs = ad._client.messages.stream.call_args
    assert kwargs["max_tokens"] == 131072


def test_max_tokens_min_with_provider_limit():
    """max_tokens=999999 + Ark api_base -> min(999999, 65536)=65536(自适应降级防 400)。"""
    ad = _make_adapter(max_tokens=999999, api_base="https://ark.cn-beijing.volces.com/api/plan")
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    _, kwargs = ad._client.messages.stream.call_args
    assert kwargs["max_tokens"] == 65536


def test_max_tokens_minimax_uses_full_ceiling():
    """max_tokens=None + Minimax api_base -> 262144(用满最高上限)。"""
    ad = _make_adapter(api_base="https://api.minimaxi.com/anthropic")
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    _, kwargs = ad._client.messages.stream.call_args
    assert kwargs["max_tokens"] == 262144


# ---- on_delta 转发 ----


def test_on_delta_forwarded_per_chunk():
    """on_delta callback 被每个 text delta 调用(拼接还原全文)。"""
    ad = _make_adapter()
    _set_stream_resp(ad._client, _fake_text_resp("hello world"), text_chunks=["hello ", "world"])
    chunks: list[str] = []
    ad.complete("s", [{"role": "user", "content": "u"}], tools=None, on_delta=chunks.append)
    assert chunks == ["hello ", "world"]


def test_on_delta_none_skips_text_stream():
    """on_delta=None 时不遍历 text_stream(直接 get_final_message,行为等价阻塞)。"""
    ad = _make_adapter()
    _set_stream_resp(ad._client, _fake_text_resp("hi"), text_chunks=["SHOULD_NOT_EMIT"])
    resp = ad.complete("s", [{"role": "user", "content": "u"}], tools=None, on_delta=None)
    assert resp == "hi"


# ---- streaming tool_use 聚合(OQ2 三端点实证)----


def test_streaming_tool_use_returns_toolcallresult():
    """get_final_message 返 tool_use block -> ToolCallResult(id/name/input 完整)。"""
    ad = _make_adapter()
    _set_stream_resp(ad._client, _fake_tool_use_resp())
    resp = ad.complete("s", [{"role": "user", "content": "u"}], tools=None)
    assert isinstance(resp, ToolCallResult)
    assert resp.tool_name == "get_weather"
    assert resp.arguments == {"city": "X"}
    assert resp.tool_call_id == "t1"
