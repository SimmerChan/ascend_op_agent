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

"""OpenAI API adapter"""

import json
import logging
from typing import Any, Callable, Optional, Union

from ascend_op_agent.agent.providers.base import (
    BaseLLMAdapter,
    ToolCallResult,
    _resolve_limit,
)

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class RateLimitError(Exception):
    """Rate limit exceeded"""

    pass


class AuthenticationError(Exception):
    """Authentication failed"""

    pass


class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI API adapter using the Chat Completions API (streaming)"""

    def __init__(self, config: Any):
        super().__init__(config)
        self.api_key = getattr(config, "api_key", "")
        self.api_base = getattr(config, "api_base", "https://api.openai.com/v1")
        self.model = getattr(config, "model", "gpt-4o")
        self.max_retries = getattr(config, "max_retries", 3)
        self.timeout = getattr(config, "timeout", 120)
        self.max_tokens = getattr(config, "max_tokens", None)

        if httpx is None:
            raise ImportError(
                "httpx is required for OpenAI adapter. Install with: pip install httpx"
            )

    def get_provider_name(self) -> str:
        return "openai"

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        tools: Optional[list[dict]] = None,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> Union[str, ToolCallResult]:
        """Send completion request to OpenAI API (streaming, SSE)

        总以 stream=True 请求,逐 SSE chunk 解析 delta.content(text) / delta.tool_calls
        (index-based 累积)。补 max_tokens(原 gap) + min provider 上限。on_delta 转发
        text delta;tool_calls 聚合后返回与 non-stream 同形的 ToolCallResult。
        """
        import time
        import httpx

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # effective max_tokens = min(config or provider_limit, provider_limit)
        limit = _resolve_limit(self.api_base)
        cfg_max = getattr(self, "max_tokens", None)
        effective = min(cfg_max, limit) if cfg_max else limit

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": effective,
            "stream": True,
        }
        if tools is not None:
            payload["tools"] = tools

        last_error = None
        for attempt in range(self.max_retries):
            try:
                text_parts: list[str] = []
                # index -> {id, name, arguments_str};OpenAI tool_calls 跨 chunk 按 index 累积
                tool_calls: dict[int, dict] = {}

                with httpx.Client(timeout=self.timeout) as client:
                    with client.stream(
                        "POST",
                        f"{self.api_base}/chat/completions",
                        headers=headers,
                        json=payload,
                    ) as resp:
                        if resp.status_code == 429:
                            wait_time = (attempt + 1) * 2
                            logger.warning(f"OpenAI rate limit, waiting {wait_time}s before retry")
                            time.sleep(wait_time)
                            continue
                        elif resp.status_code == 401:
                            raise AuthenticationError(f"Invalid API key: {resp.text}")
                        elif resp.status_code != 200:
                            err_text = resp.read().decode("utf-8", errors="replace")
                            try:
                                err_body = json.loads(err_text)
                                err_msg = (
                                    err_body.get("error", {}).get("message")
                                    or err_body.get("message")
                                    or err_text[:300]
                                )
                            except (json.JSONDecodeError, ValueError):
                                err_msg = err_text[:300]
                            last_error = f"HTTP {resp.status_code}: {err_msg}"
                            if (
                                resp.status_code in (402, 403)
                                or "余额不足" in err_msg
                                or "用量上限" in err_msg
                            ):
                                raise Exception(
                                    f"OpenAI API quota/rate limit (no retry): {last_error}"
                                )
                            if attempt < self.max_retries - 1:
                                logger.warning(
                                    f"HTTP {resp.status_code}: {err_msg}, "
                                    f"attempt {attempt + 1}/{self.max_retries}"
                                )
                                time.sleep(1)
                                continue
                            raise Exception(f"OpenAI API error {last_error}")

                        # 解析 SSE:每行 "data: {...}" 或 "data: [DONE]"
                        for line in resp.iter_lines():
                            if not line:
                                continue
                            if line.startswith("data: "):
                                data_str = line[6:]
                            elif line.startswith("data:"):
                                data_str = line[5:]
                            else:
                                continue
                            data_str = data_str.strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                # chunk 边界截断 → 跳过本行(健壮)
                                continue
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {}) or {}

                            # text delta
                            content = delta.get("content")
                            if content:
                                text_parts.append(content)
                                if on_delta is not None:
                                    on_delta(content)

                            # tool_calls delta(index-based fragment 合并)
                            for tc in delta.get("tool_calls", []) or []:
                                idx = tc.get("index", 0)
                                slot = tool_calls.setdefault(
                                    idx, {"id": None, "name": None, "arguments": ""}
                                )
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function", {}) or {}
                                if fn.get("name"):
                                    slot["name"] = fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]

                # 聚合结果(同 non-stream 形:ToolCallResult 或 text)
                if tool_calls:
                    first = tool_calls[min(tool_calls.keys())]
                    args_str = first.get("arguments") or "{}"
                    try:
                        args = json.loads(args_str) if args_str.strip() else {}
                    except json.JSONDecodeError:
                        # tool_use input 未完整(stream 中断)→ 空 dict + 重试链路兜底
                        args = {}
                    return ToolCallResult(
                        tool_call_id=first.get("id") or "",
                        tool_name=first.get("name") or "",
                        arguments=args,
                        raw_response={"tool_call": first, "text": "".join(text_parts)},
                    )

                return "".join(text_parts)

            except httpx.TimeoutException as e:
                last_error = f"TimeoutException: {e}"
                logger.warning(f"Request timeout ({e}), attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

            except httpx.RequestError as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Request error: {type(e).__name__}: {e}, "
                    f"attempt {attempt + 1}/{self.max_retries}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Unexpected error: {type(e).__name__}: {e}, "
                    f"attempt {attempt + 1}/{self.max_retries}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        raise Exception(f"OpenAI request failed after {self.max_retries} attempts: {last_error}")
