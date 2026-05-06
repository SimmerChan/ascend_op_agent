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

"""Ollama API adapter for local LLM models"""

import logging
from typing import Any

from ascend_op_agent.agent.providers.base import BaseLLMAdapter

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class OllamaAdapter(BaseLLMAdapter):
    """Ollama API adapter for local models (OpenAI-compatible endpoint)"""

    def __init__(self, config: Any):
        super().__init__(config)
        # Ollama may not require an API key for local usage
        self.api_key = getattr(config, 'api_key', 'ollama')
        self.api_base = getattr(config, 'api_base', 'http://localhost:11434/v1')
        self.model = getattr(config, 'model', 'llama3')
        self.max_retries = getattr(config, 'max_retries', 3)
        self.timeout = getattr(config, 'timeout', 300)  # Longer timeout for local models

        if httpx is None:
            raise ImportError("httpx is required for Ollama adapter. Install with: pip install httpx")

    def get_provider_name(self) -> str:
        return "ollama"

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """Send completion request to Ollama API

        Ollama uses OpenAI-compatible API format at http://localhost:11434/v1

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

        # Build headers - Ollama typically doesn't require auth for local
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

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

                elif response.status_code == 404:
                    # Ollama might not have the model pulled
                    raise Exception(f"Model '{self.model}' not found. Run: ollama pull {self.model}")

                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Ollama rate limit, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue

                else:
                    raise Exception(f"Ollama API error {response.status_code}: {response.text}")

            except httpx.TimeoutException:
                last_error = "Request timed out - local model may be slow to respond"
                logger.warning(f"Request timeout, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)  # Longer sleep for local models
                continue

            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(f"Request error: {e}, attempt {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    time.sleep(2)
                continue

        raise Exception(f"Ollama request failed after {self.max_retries} attempts: {last_error}")
