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

"""Azure OpenAI API adapter"""

import logging
from typing import Any

from ascend_op_agent.agent.providers.base import BaseLLMAdapter

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class AzureOpenAIAdapter(BaseLLMAdapter):
    """Azure OpenAI API adapter using Chat Completions API"""

    def __init__(self, config: Any):
        super().__init__(config)
        self.api_key = getattr(config, "api_key", "")
        # Azure OpenAI uses deployment name as model
        self.deployment = getattr(config, "model", "gpt-4o")
        # Azure OpenAI endpoint format: https://{resource}.openai.azure.com/openai/deployments/{deployment}
        self.api_base = getattr(config, "api_base", "")
        self.api_version = getattr(config, "api_version", "2024-02-01")
        self.max_retries = getattr(config, "max_retries", 3)
        self.timeout = getattr(config, "timeout", 120)

        if httpx is None:
            raise ImportError(
                "httpx is required for Azure OpenAI adapter. Install with: pip install httpx"
            )

    def get_provider_name(self) -> str:
        return "azure"

    def _build_endpoint(self) -> str:
        """Build the Azure OpenAI endpoint URL"""
        if self.api_base:
            return f"{self.api_base}/chat/completions?api-version={self.api_version}"
        raise ValueError(
            "Azure OpenAI api_base is required (e.g., https://your-resource.openai.azure.com)"
        )

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
        on_delta=None,
    ) -> str:
        """Send completion request to Azure OpenAI API

        Args:
            system_prompt: System prompt
            conversation_history: List of {'role': str, 'content': str}

        Returns:
            Response text from the model
        """
        import time

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        headers = {
            "Content-Type": "application/json",
        }

        # Azure OpenAI uses api-key header instead of Authorization Bearer
        if self.api_key:
            headers["api-key"] = self.api_key

        payload = {
            "messages": messages,
            "temperature": 0.7,
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self._build_endpoint(),
                        headers=headers,
                        json=payload,
                    )

                if response.status_code == 200:
                    data = response.json()
                    return data["choices"][0]["message"]["content"]

                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Azure OpenAI rate limit, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue

                elif response.status_code in (401, 403):
                    raise Exception(f"Azure OpenAI authentication failed: {response.text}")

                else:
                    raise Exception(
                        f"Azure OpenAI API error {response.status_code}: {response.text}"
                    )

            except httpx.TimeoutException:
                last_error = "Request timed out"
                logger.warning(f"Request timeout, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(f"Request error: {e}, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(1)
                continue

        raise Exception(
            f"Azure OpenAI request failed after {self.max_retries} attempts: {last_error}"
        )
