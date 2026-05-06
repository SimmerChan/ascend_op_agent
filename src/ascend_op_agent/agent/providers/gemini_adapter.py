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

"""Google Gemini API adapter"""

import logging
from typing import Any

from ascend_op_agent.agent.providers.base import BaseLLMAdapter

logger = logging.getLogger(__name__)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


class GeminiAdapter(BaseLLMAdapter):
    """Google Gemini API adapter using the native REST API"""

    def __init__(self, config: Any):
        super().__init__(config)
        self.api_key = getattr(config, 'api_key', '')
        self.model = getattr(config, 'model', 'gemini-2.5-flash')
        self.api_base = getattr(config, 'api_base', 'https://generativelanguage.googleapis.com/v1beta')
        self.max_retries = getattr(config, 'max_retries', 3)
        self.timeout = getattr(config, 'timeout', 120)

    def get_provider_name(self) -> str:
        return "gemini"

    def complete(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """Send completion request to Gemini API

        Args:
            system_prompt: System prompt
            conversation_history: List of {'role': str, 'content': str}

        Returns:
            Response text from the model
        """
        import time

        # Build messages for Gemini
        # Gemini uses contents[] with role and parts
        contents = []
        for msg in conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        # Add system prompt as the first user message if present
        if system_prompt and not contents:
            contents.insert(0, {
                "role": "user",
                "parts": [{"text": system_prompt}]
            })
        elif system_prompt:
            # Prepend system instruction
            contents.insert(0, {
                "role": "user",
                "parts": [{"text": f"System instructions: {system_prompt}"}]
            })

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192,
            },
        }

        url = f"{self.api_base}/models/{self.model}:generateContent"

        last_error = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        url,
                        params={"key": self.api_key},
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )

                if response.status_code == 200:
                    data = response.json()
                    # Gemini returns candidates[0].content.parts[0].text
                    candidates = data.get("candidates", [])
                    if (candidates and
                        len(candidates) > 0 and
                        candidates[0].get("content", {}).get("parts") and
                        len(candidates[0]["content"]["parts"]) > 0):
                        return candidates[0]["content"]["parts"][0]["text"]
                    return str(data)

                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"Gemini rate limit, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue

                elif response.status_code == 401:
                    raise Exception(f"Invalid API key: {response.text}")

                else:
                    raise Exception(f"Gemini API error {response.status_code}: {response.text}")

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

        raise Exception(f"Gemini request failed after {self.max_retries} attempts: {last_error}")
