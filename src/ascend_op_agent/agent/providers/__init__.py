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

"""LLM Provider adapters module"""

from ascend_op_agent.agent.providers.base import BaseLLMAdapter
from ascend_op_agent.agent.providers.openai_adapter import OpenAIAdapter
from ascend_op_agent.agent.providers.anthropic_adapter import AnthropicAdapter
from ascend_op_agent.agent.providers.gemini_adapter import GeminiAdapter
from ascend_op_agent.agent.providers.openrouter_adapter import OpenRouterAdapter
from ascend_op_agent.agent.providers.azure_adapter import AzureOpenAIAdapter
from ascend_op_agent.agent.providers.ollama_adapter import OllamaAdapter

__all__ = [
    "BaseLLMAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "OpenRouterAdapter",
    "AzureOpenAIAdapter",
    "OllamaAdapter",
]