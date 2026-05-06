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

import logging
from typing import Any

from ascend_op_agent.agent.providers.base import BaseLLMAdapter

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
    """OpenAI API adapter using the Chat Completions API"""

    def __init__(self, config: Any):
        super().__init__(config)
        self.api_key = getattr(config, 'api_key', '')
        self.api_base = getattr(config, 'api_base', 'https://api.openai.com/v1')
        self.model = getattr(config, 'model', 'gpt-4o')
        self.max_retries = getattr(config, 'max_retries', 3)
        self.timeout = getattr(config, 'timeout', 120)

        if httpx is None:
            raise ImportError("httpx is required for OpenAI adapter. Install with: pip install httpx")

    def get_provider_name(self) -> str:
        return "openai"

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """Send completion request to OpenAI API

        Args:
            system_prompt: System prompt
            conversation_history: List of {'role': str, 'content': str}

        Returns:
            Response text from the model
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

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        f"{self.api_base}/chat/completions",
                        headers=headers,
                        json=payload,
                    )

                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]

                elif response.status_code == 429:
                    # Rate limit - exponential backoff
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"OpenAI rate limit, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue

                elif response.status_code == 401:
                    raise AuthenticationError(f"Invalid API key: {response.text}")

                else:
                    raise Exception(f"OpenAI API error {response.status_code}: {response.text}")

            except httpx.TimeoutException:
                logger.warning(f"Request timeout, attempt {attempt + 1}/{self.max_retries}")
                last_error = "Request timed out"
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(f"Request error: {e}, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        raise Exception(f"OpenAI request failed after {self.max_retries} attempts: {last_error}")