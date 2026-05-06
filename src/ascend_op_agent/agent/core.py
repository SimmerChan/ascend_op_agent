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

"""AIAgent - Agent核心引擎

负责会话管理、迭代控制和工具调用。
"""

import logging
import time
from typing import Any, Optional

from ascend_op_agent.agent.context import ContextEngine
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.agent.providers import (
    OpenAIAdapter,
    AnthropicAdapter,
    BaseLLMAdapter,
)
from ascend_op_agent.agent.tool_registry import ToolRegistry
from ascend_op_agent.config import Config

logger = logging.getLogger(__name__)


class AIAgent:
    """Ascend Op Agent 核心引擎

    负责:
    - 会话管理和迭代控制
    - 7层Prompt组装
    - 工具注册和调用
    - 上下文和记忆管理
    """

    def __init__(
        self,
        config: Config,
        tool_registry: ToolRegistry,
        prompt_builder: PromptBuilder,
        context_engine: ContextEngine,
        memory_store: MemoryStore,
    ):
        self.config = config
        self.tool_registry = tool_registry
        self.prompt_builder = prompt_builder
        self.context_engine = context_engine
        self.memory = memory_store

        self._llm_client = LLMClient(config.llm)
        self._conversation_history: list[dict[str, str]] = []
        self._max_iterations = 10
        self._current_iteration = 0

    def run_conversation(self, user_input: str) -> str:
        """运行对话

        Args:
            user_input: 用户输入

        Returns:
            Agent响应
        """
        self._conversation_history.append({"role": "user", "content": user_input})
        self._current_iteration = 0

        # 冻结记忆快照（会话级一致性）
        self.memory.freeze_snapshot()

        while self._current_iteration < self._max_iterations:
            self._current_iteration += 1

            # 1. 组装7层Prompt
            system_prompt = self.prompt_builder.build_system_prompt(
                workspace_path=self.config.local.workspace,
                memory_store=self.memory,
            )

            # 2. 调用LLM
            response = self._llm_client.call(
                system_prompt=system_prompt,
                conversation_history=self._conversation_history,
            )

            # 3. 解析响应（可能是工具调用或直接回复）
            if self._is_tool_call(response):
                tool_result = self._execute_tool_call(response)
                self._conversation_history.append({
                    "role": "assistant",
                    "content": response,
                })
                self._conversation_history.append({
                    "role": "tool",
                    "content": tool_result,
                })
                # 继续迭代
            else:
                # 直接回复
                self._conversation_history.append({"role": "assistant", "content": response})
                # 更新记忆
                self._update_memory(user_input, response)
                return response

        # 达到最大迭代次数
        return "已达到最大迭代次数，请尝试简化您的问题。"

    def _is_tool_call(self, response: str) -> bool:
        """检查响应是否为工具调用"""
        return response.strip().startswith("<tool_call>")

    def _execute_tool_call(self, response: str) -> str:
        """执行工具调用"""
        # 解析工具调用 XML 格式
        # 格式: <tool_call name="tool_name">{"arg": "value"}</tool_call>
        import re

        match = re.match(r'<tool_call name="(\w+)">(.+?)</tool_call>', response, re.DOTALL)
        if not match:
            return "错误: 无效的工具调用格式"

        tool_name = match.group(1)
        args_str = match.group(2)

        import json
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            return f"错误: 无效的JSON参数: {args_str}"

        # 执行工具
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return f"错误: 未知工具: {tool_name}"

        try:
            result = tool.execute(**args)
            return str(result)
        except Exception as e:
            return f"错误: 工具执行失败: {e}"

    def _update_memory(self, user_input: str, response: str) -> None:
        """更新记忆"""
        # 添加到user pool（用户偏好）
        self.memory.add("user", f"User asked about: {user_input[:100]}")

        # 添加到memory pool（Agent记忆）
        self.memory.add("memory", f"Response: {response[:100]}")

    def reset_conversation(self) -> None:
        """重置对话历史"""
        self._conversation_history = []
        self._current_iteration = 0

    @property
    def tools(self) -> list[str]:
        """获取已注册工具列表"""
        return self.tool_registry.list_tools()


class LLMClient:
    """LLM client with multi-provider support"""

    _ADAPTERS = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
    }

    def __init__(self, llm_config):
        self.config = llm_config
        self.max_retries = getattr(llm_config, 'max_retries', 3)
        self.backoff_factor = 2
        self.timeout = getattr(llm_config, 'timeout', 120)

        # Create adapter based on provider
        provider = getattr(llm_config, 'provider', 'openai').lower()
        adapter_class = self._ADAPTERS.get(provider)

        if adapter_class is None:
            raise ValueError(
                f"Unsupported LLM provider: {provider}. "
                f"Supported providers: {list(self._ADAPTERS.keys())}"
            )

        self._adapter: BaseLLMAdapter = adapter_class(llm_config)
        logger.info(f"LLM client initialized with provider: {provider}")

    def call(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, str]],
    ) -> str:
        """Call the LLM using the configured provider adapter

        Args:
            system_prompt: System prompt for the conversation
            conversation_history: List of message dicts with 'role' and 'content'

        Returns:
            LLM response text
        """
        return self._adapter.complete(system_prompt, conversation_history)


class RateLimitError(Exception):
    """速率限制错误"""
    pass


class ServiceUnavailableError(Exception):
    """服务不可用错误"""
    pass


class MaxRetriesExceededError(Exception):
    """超过最大重试次数"""
    pass
