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

"""Anthropic API adapter"""

import logging
from typing import Any, Optional, Union

from ascend_op_agent.agent.providers.base import BaseLLMAdapter, ToolCallResult

logger = logging.getLogger(__name__)

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]


class RateLimitError(Exception):
    """Rate limit exceeded"""

    pass


class AuthenticationError(Exception):
    """Authentication failed"""

    pass


class AnthropicAdapter(BaseLLMAdapter):
    """Anthropic Messages API adapter"""

    def __init__(self, config: Any):
        super().__init__(config)
        self.api_key = getattr(config, "api_key", "")
        self.auth_token = getattr(config, "auth_token", "")
        self.api_base = getattr(config, "api_base", None)
        self.model = getattr(config, "model", "claude-sonnet-4-6-20250514")
        self.max_retries = getattr(config, "max_retries", 3)
        self.timeout = getattr(config, "timeout", 120)
        self.disable_thinking = getattr(config, "disable_thinking", False)
        self.max_tokens = getattr(config, "max_tokens", 16384)

        if anthropic is None:
            raise ImportError(
                "anthropic is required for Anthropic adapter. Install with: pip install anthropic"
            )

        self._client = None

    def get_provider_name(self) -> str:
        return "anthropic"

    def _get_client(self):
        """Get or create Anthropic client"""
        if self._client is None:
            # auth_token 非空 -> Bearer auth(Ark 等用 Authorization: Bearer);
            # 否则 api_key -> x-api-key(Anthropic 官方 / Minimax / GLM /api/anthropic)。
            client_kwargs = {"timeout": self.timeout}
            if self.auth_token:
                client_kwargs["auth_token"] = self.auth_token
            else:
                client_kwargs["api_key"] = self.api_key
            if self.api_base:
                client_kwargs["base_url"] = self.api_base
            self._client = anthropic.Anthropic(**client_kwargs)
        return self._client

    def _convert_tools_to_anthropic_format(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI function format tools to Anthropic tool format

        OpenAI format:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

        Anthropic format:
            {"name": ..., "description": ..., "input_schema": ...}
        """
        anthropic_tools = []
        for tool in tools:
            if tool.get("type") == "function" and "function" in tool:
                func = tool["function"]
                anthropic_tools.append(
                    {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {}),
                    }
                )
            elif "name" in tool:
                # Already in Anthropic format or named tool
                anthropic_tools.append(tool)
        return anthropic_tools

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        tools: Optional[list[dict]] = None,
    ) -> Union[str, ToolCallResult]:
        """Send completion request to Anthropic API

        Args:
            system_prompt: System prompt
            conversation_history: List of {'role': str, 'content': str}
            tools: Optional list of tool definitions in Anthropic format

        Returns:
            Response text from the model, or ToolCallResult if a tool call is triggered
        """
        import time

        # Build messages for Anthropic
        # Anthropic uses roles: user, assistant (not system)
        # System prompt goes in top_level_system_prompt parameter
        messages = []
        for msg in conversation_history:
            role = msg["role"]
            content = msg["content"]
            if role == "assistant" and "tool_use_id" in msg:
                # Native tool_use: 重建 assistant content 为 tool_use block list
                # (core.py ToolCallResult 分支存的元数据,修复多轮 tool_use 断裂)
                messages.append(
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": msg["tool_use_id"],
                                "name": msg["tool_name"],
                                "input": msg["tool_input"],
                            }
                        ],
                    }
                )
            elif role == "tool" and "tool_use_id" in msg:
                # native tool_result block (匹配上方 tool_use.id)
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg["tool_use_id"],
                                "content": content,
                            }
                        ],
                    }
                )
            else:
                # 旧格式 role=tool(无 tool_use_id,XML 分支)或未知 role → 降级 user + str
                role = role if role in ("user", "assistant") else "user"
                messages.append({"role": role, "content": content})

        client = self._get_client()

        # Convert tools from OpenAI format to Anthropic format if provided
        anthropic_tools = self._convert_tools_to_anthropic_format(tools) if tools else None

        last_error = None
        for attempt in range(self.max_retries):
            try:
                create_kwargs = dict(
                    model=self.model,
                    max_tokens=getattr(self, "max_tokens", 16384),
                    system=system_prompt,
                    messages=messages,
                    tools=anthropic_tools,
                )
                if getattr(self, "disable_thinking", False):
                    # 推理模型(glm-5.2/Ark)thinking 会吃光 max_tokens 致 text 空(SOUL
                    # system 下实测 thinking 12k-15k 字符 vs max_tokens=4096)。禁 thinking
                    # 让模型直出 visible text。非推理模型(MiniMax-M3)disable_thinking=False
                    # 不传该参数(其兼容端点可能不认 thinking)。
                    create_kwargs["thinking"] = {"type": "disabled"}
                response = client.messages.create(**create_kwargs)

                # Check for tool_use blocks (Native Function Calling)
                for block in response.content:
                    if block.type == "tool_use":
                        return ToolCallResult(
                            tool_call_id=block.id,
                            tool_name=block.name,
                            arguments=block.input,
                            raw_response=response,
                        )

                # Return text content
                for block in response.content:
                    if block.type == "text":
                        return block.text

                raise Exception("No text or tool_use block in response")

            except anthropic.RateLimitError as e:
                # 检查是 transient rate limit 还是 quota exhausted
                err_str = str(e)
                if "用量上限" in err_str or "余额不足" in err_str or "quota" in err_str.lower():
                    raise Exception(f"Anthropic API quota exhausted (no retry): {err_str}") from e
                wait_time = (attempt + 1) * 2
                logger.warning(f"Anthropic rate limit, waiting {wait_time}s before retry")
                time.sleep(wait_time)
                continue

            except anthropic.AuthenticationError:
                raise AuthenticationError(f"Invalid API key")

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Anthropic request error: {type(e).__name__}: {e}, "
                    f"attempt {attempt + 1}/{self.max_retries}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        raise Exception(f"Anthropic request failed after {self.max_retries} attempts: {last_error}")
