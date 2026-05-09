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
from typing import Any, Optional


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
    ) -> str:
        """Send a completion request to the LLM provider

        Args:
            system_prompt: System prompt for the conversation
            conversation_history: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions in OpenAI function format

        Returns:
            The LLM's response text

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