# Copyright 2026 SimmerChan
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

"""Base LLM adapter interface"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union


@dataclass
class ToolCallResult:
    """结构化工具调用结果

    用于 Native Function Calling 模式，从 Provider 的原生响应中解析。
    """

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    raw_response: Any


# Per-provider max_tokens 真实上限(2026-07-29 三端点探测实证)
# Minimax 256K / GLM 官方 128K / Ark 64K。>32K non-stream 被 anthropic SDK
# 10min 长请求保护拒,故 adapter 走 streaming。按 api_base 子串匹配;未匹配 fallback。
PROVIDER_MAX_TOKENS: dict[str, int] = {
    "api.minimaxi.com": 262144,  # Minimax MiniMax-M3
    "open.bigmodel.cn": 131072,  # GLM 官方 glm-5.2
    "ark.cn-beijing.volces.com": 65536,  # Ark glm-5.2
}
_DEFAULT_MAX_TOKENS_FALLBACK = 65536


def _resolve_limit(api_base: Optional[str]) -> int:
    """按 api_base 子串匹配 provider 真实 max_tokens 上限;未匹配返保守 fallback。"""
    if not api_base:
        return _DEFAULT_MAX_TOKENS_FALLBACK
    for host, limit in PROVIDER_MAX_TOKENS.items():
        if host in api_base:
            return limit
    return _DEFAULT_MAX_TOKENS_FALLBACK


class BaseLLMAdapter(ABC):
    """LLM provider adapter base class"""

    def __init__(self, config: Any):
        """Initialize adapter with configuration

        Args:
            config: Provider-specific configuration object
        """
        self.config = config

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        tools: Optional[list[dict]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Union[str, ToolCallResult]:
        """Send a completion request to the LLM provider

        Args:
            system_prompt: System prompt for the conversation
            conversation_history: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions in OpenAI function format
            on_delta: Optional streaming callback invoked per text delta chunk
                (None = no streaming delta forwarding; adapter still streams internally
                to support large max_tokens beyond the SDK 10min non-stream guard).

        Returns:
            The LLM's response text, or ToolCallResult if a tool call is triggered

        Raises:
            RateLimitError: When rate limited, caller should retry with backoff
            ServiceUnavailableError: When the service is down
            AuthenticationError: When API key is invalid
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name (e.g., 'openai', 'anthropic')"""
        pass
