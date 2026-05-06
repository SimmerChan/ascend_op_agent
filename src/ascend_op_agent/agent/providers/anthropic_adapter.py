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
from typing import Any

from ascend_op_agent.agent.providers.base import BaseLLMAdapter

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
        self.api_key = getattr(config, 'api_key', '')
        self.api_base = getattr(config, 'api_base', None)
        self.model = getattr(config, 'model', 'claude-sonnet-4-6-20250514')
        self.max_retries = getattr(config, 'max_retries', 3)
        self.timeout = getattr(config, 'timeout', 120)

        if anthropic is None:
            raise ImportError("anthropic is required for Anthropic adapter. Install with: pip install anthropic")

        self._client = None

    def get_provider_name(self) -> str:
        return "anthropic"

    def _get_client(self):
        """Get or create Anthropic client"""
        if self._client is None:
            client_kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.api_base:
                client_kwargs["base_url"] = self.api_base
            self._client = anthropic.Anthropic(**client_kwargs)
        return self._client

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """Send completion request to Anthropic API

        Args:
            system_prompt: System prompt
            conversation_history: List of {'role': str, 'content': str}

        Returns:
            Response text from the model
        """
        import time

        # Build messages for Anthropic
        # Anthropic uses roles: user, assistant (not system)
        # System prompt goes in top_level_system_prompt parameter
        messages = []
        for msg in conversation_history:
            role = msg["role"] if msg["role"] in ("user", "assistant") else "user"
            messages.append({"role": role, "content": msg["content"]})

        client = self._get_client()

        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                )
                for block in response.content:
                    if block.type == "text":
                        return block.text
                raise Exception("No text block in response")

            except anthropic.RateLimitError:
                wait_time = (attempt + 1) * 2
                logger.warning(f"Anthropic rate limit, waiting {wait_time}s before retry")
                time.sleep(wait_time)
                continue

            except anthropic.AuthenticationError:
                raise AuthenticationError(f"Invalid API key")

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Anthropic request error: {e}, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        raise Exception(f"Anthropic request failed after {self.max_retries} attempts: {last_error}")