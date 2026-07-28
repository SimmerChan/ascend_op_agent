"""AnthropicAdapter thinking 参数单测(修复推理模型 thinking 吃光 max_tokens)。

根因(2026-07-23 910B spike 实测):glm-5.2/Ark 推理模型在 SOUL 等长 system_prompt 下
thinking 膨胀到 12k-15k 字符,吃光 max_tokens=4096 预算(stop_reason=max_tokens),
visible text block 输出 0 字符 -> adapter return "" -> codegen 全空。

修复:LLMConfig.disable_thinking=True 时 adapter 传 thinking={type:disabled};
False(MiniMax-M3 等非推理模型,其兼容端点可能不认 thinking 参数)不传。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ascend_op_agent.agent.providers.anthropic_adapter import AnthropicAdapter


def _cfg(disable_thinking: bool = False, max_tokens: int = 16384) -> SimpleNamespace:
    return SimpleNamespace(
        api_key="k",
        auth_token="",
        api_base="http://x",
        model="m",
        max_retries=1,
        timeout=10,
        disable_thinking=disable_thinking,
        max_tokens=max_tokens,
    )


def _fake_text_resp(text: str = "hi"):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block], stop_reason="end_turn")


def _make_adapter(disable_thinking: bool) -> AnthropicAdapter:
    ad = AnthropicAdapter(_cfg(disable_thinking))
    ad._client = MagicMock()
    ad._client.messages.create.return_value = _fake_text_resp()
    return ad


def test_disable_thinking_true_passes_disabled_param():
    """disable_thinking=True -> create kwargs 含 thinking={type:disabled}。"""
    ad = _make_adapter(True)
    ad.complete(
        system_prompt="s",
        conversation_history=[{"role": "user", "content": "u"}],
        tools=None,
    )
    _, kwargs = ad._client.messages.create.call_args
    assert kwargs.get("thinking") == {"type": "disabled"}


def test_disable_thinking_false_omits_param():
    """disable_thinking=False -> create kwargs 不含 thinking(保护 Minimax 等兼容端点)。"""
    ad = _make_adapter(False)
    ad.complete(
        system_prompt="s",
        conversation_history=[{"role": "user", "content": "u"}],
        tools=None,
    )
    _, kwargs = ad._client.messages.create.call_args
    assert "thinking" not in kwargs, "非推理模型不应传 thinking 参数"


def test_disable_thinking_returns_text_not_empty():
    """回归:禁 thinking 后 adapter 正常返回 text block 内容(非空)。"""
    ad = _make_adapter(True)
    ad._client.messages.create.return_value = _fake_text_resp("```cpp\n// x\nCODE\n```")
    resp = ad.complete(
        system_prompt="s",
        conversation_history=[{"role": "user", "content": "u"}],
        tools=None,
    )
    assert resp == "```cpp\n// x\nCODE\n```"


# ---- max_tokens(thinking + text 共享预算,默认 16384 兜底) ----


def test_max_tokens_default_16384():
    """adapter max_tokens 默认 16384(不硬编码 4096,避免 thinking 吃光)。"""
    ad = AnthropicAdapter(_cfg())
    assert ad.max_tokens == 16384


def test_max_tokens_override_from_config():
    """config 设 max_tokens -> adapter 用配置值。"""
    ad = AnthropicAdapter(_cfg(max_tokens=8192))
    assert ad.max_tokens == 8192


def test_max_tokens_passed_to_create():
    """complete() 把 self.max_tokens 传给 client.messages.create(不硬编码 4096)。"""
    ad = AnthropicAdapter(_cfg(max_tokens=8192))
    ad._client = MagicMock()
    ad._client.messages.create.return_value = _fake_text_resp("ok")
    ad.complete(
        system_prompt="s",
        conversation_history=[{"role": "user", "content": "u"}],
        tools=None,
    )
    _, kwargs = ad._client.messages.create.call_args
    assert kwargs["max_tokens"] == 8192
