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
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Optional

from ascend_op_agent.agent.context import ContextEngine
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.agent.providers import (
    OpenAIAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    OpenRouterAdapter,
    AzureOpenAIAdapter,
    OllamaAdapter,
    BaseLLMAdapter,
)
from ascend_op_agent.agent.providers.base import ToolCallResult
from ascend_op_agent.agent.session_manager import SessionRecordManager
from ascend_op_agent.agent.session_record import (
    Entry,
    LLMEntry,
    SystemEntry,
    ToolEntry,
    UserEntry,
)
from ascend_op_agent.agent.tool_registry import ToolRegistry
from ascend_op_agent.config import Config

# 导入工具包以触发自注册
from ascend_op_agent.agent import tools  # noqa: F401

if TYPE_CHECKING:
    pass

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
        session_manager: Optional[SessionRecordManager] = None,
        tool_progress_callback: Optional[Callable[..., None]] = None,
        status_callback: Optional[Callable[..., None]] = None,
    ):
        self.config = config
        self.tool_registry = tool_registry
        self.prompt_builder = prompt_builder
        self.context_engine = context_engine
        self.memory = memory_store
        self._session_manager = session_manager
        self._tool_progress_callback = tool_progress_callback
        self._status_callback = status_callback

        self._llm_client = LLMClient(config.llm)
        self._conversation_history: list[dict[str, str]] = []
        # 工具调用 side-channel:每次 LLM 触发工具执行时记录一行(供编排器
        # 提取结构化结果,如 file_write 实际写到的路径)。不进 _conversation_history
        # 以保持 P0-2 同形契约。
        self._tool_calls_log: list[dict] = []
        self._max_iterations = 10
        self._current_iteration = 0
        self._current_session_id: Optional[str] = None

    def _safe_append(self, entry: Entry) -> None:
        """安全追加 entry，session_manager 为 None 时不抛异常"""
        if self._session_manager is not None:
            self._session_manager.append_entry(entry)

    def _get_or_create_session_id(self) -> str:
        """获取或创建当前会话 ID"""
        if self._current_session_id is None:
            self._current_session_id = str(uuid.uuid4())
        return self._current_session_id

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override: Optional[str] = None,
    ) -> str:
        """运行对话

        Args:
            user_input: 用户输入
            skills_layer_override: 可选,注入该阶段 cannbot skill 包替换 Layer 6。
                由编排器 LLM 节点传入(hybrid 集成);默认 None 保持原行为。

        Returns:
            Agent响应
        """
        session_id = self._get_or_create_session_id()
        model = self.config.llm.model
        provider = self.config.llm.provider

        # 记录用户输入
        user_entry_id = str(uuid.uuid4())
        self._safe_append(UserEntry(
            id=user_entry_id,
            timestamp=time.time(),
            session_id=session_id,
            model=model,
            provider=provider,
            turn_id=0,
            content=user_input,
        ))

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
                skills_layer_override=skills_layer_override,
            )

            # 记录 system prompt
            system_entry_id = str(uuid.uuid4())
            self._safe_append(SystemEntry(
                id=system_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                content=system_prompt,
            ))

            # 2. 调用LLM
            # 构建 input_messages
            input_messages = [{"role": "system", "content": system_prompt}] + self._conversation_history
            tools = self.tool_registry.to_openai_format()

            llm_entry_id = str(uuid.uuid4())
            llm_entry_parent_id = user_entry_id  # LLMEntry.parent_id = UserEntry.id

            # 触发 thinking 状态回调
            if self._status_callback:
                try:
                    self._status_callback("thinking")
                except Exception as e:
                    logger.warning(f"status_callback error: {e}")

            # 启动超时追踪器
            timeout_tracker = TimeoutTracker(60, lambda: self._status_callback("waiting") if self._status_callback else None)
            timeout_tracker.start()

            try:
                response = self._llm_client.call(
                    system_prompt=system_prompt,
                    conversation_history=self._conversation_history,
                    tools=tools if tools else None,
                )
            finally:
                timeout_tracker.cancel()

            # 触发 idle 状态回调
            if self._status_callback:
                try:
                    self._status_callback("idle")
                except Exception as e:
                    logger.warning(f"status_callback error: {e}")

            # 解析 tool_calls（如果有）
            tool_calls = self._parse_tool_calls(response)

            # 记录 LLMEntry
            self._safe_append(LLMEntry(
                id=llm_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=llm_entry_parent_id,
                input_messages=input_messages,
                output_content=response,
                tool_calls=tool_calls,
            ))

            # 检查是否为 Native Function Calling 响应
            if isinstance(response, ToolCallResult):
                # Native Function Calling 模式：直接使用结构化数据
                tool_result = self._execute_tool_call_from_result(response, llm_entry_id)
                self._conversation_history.append({
                    "role": "assistant",
                    "content": f"tool_call({response.tool_name})",
                })
                self._conversation_history.append({
                    "role": "tool",
                    "content": tool_result,
                })
                # 继续迭代
            elif self._is_tool_call(response):
                # 旧版 XML 格式兼容
                tool_result = self._execute_tool_call(response, llm_entry_id)
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

                # 触发 completed 状态回调
                if self._status_callback:
                    try:
                        self._status_callback("completed")
                    except Exception as e:
                        logger.warning(f"status_callback error: {e}")

                return response

        # 达到最大迭代次数
        return "已达到最大迭代次数，请尝试简化您的问题。"

    def _is_tool_call(self, response: str) -> bool:
        """检查响应是否为工具调用"""
        # Support both XML format (<tool_call> or <TOOL_CALL>) and multi-line tool_calls block
        response_lower = response.lower()
        return "<tool_call" in response_lower and "</tool_call>" in response_lower

    def _parse_tool_calls(self, response: str) -> list[dict[str, Any]]:
        """解析 LLM 响应中的工具调用

        Args:
            response: LLM 响应文本

        Returns:
            工具调用列表
        """
        if isinstance(response, ToolCallResult):
            # Native Function Calling 模式
            return [{
                "tool_call_id": response.tool_call_id,
                "name": response.tool_name,
                "arguments": response.arguments,
            }]
        elif self._is_tool_call(response):
            # XML 格式
            import re
            import json
            tool_calls = []
            matches = re.findall(
                r'<tool_call\s+name="(\w+)">(.+?)</tool_call>',
                response,
                re.DOTALL | re.IGNORECASE
            )
            for match in matches:
                tool_name = match[0]
                args_str = match[1].strip()
                try:
                    args = json.loads(args_str)
                    tool_calls.append({
                        "name": tool_name,
                        "arguments": args,
                    })
                except json.JSONDecodeError:
                    pass
            return tool_calls
        return []

    def _execute_tool_call(self, response: str, parent_id: str) -> str:
        """执行工具调用

        Args:
            response: LLM 响应文本
            parent_id: 父节点 ID（LLMEntry 的 id）
        """
        # 解析工具调用 XML 格式
        # 格式: <tool_call name="tool_name">{"arg": "value"}</tool_call>
        import re

        # Match the first tool_call block (may span multiple lines), case-insensitive
        match = re.search(r'<tool_call\s+name="(\w+)">(.+?)</tool_call>', response, re.DOTALL | re.IGNORECASE)
        if not match:
            return "错误: 无效的工具调用格式"

        tool_name = match.group(1)
        args_str = match.group(2).strip()

        import json
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            return f"错误: 无效的JSON参数: {args_str}"

        # 执行工具
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return f"错误: 未知工具: {tool_name}"

        session_id = self._get_or_create_session_id()
        model = self.config.llm.model
        provider = self.config.llm.provider

        try:
            # 触发 tool.started 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.started", tool_name)
                except Exception as e:
                    logger.warning(f"tool_progress_callback error: {e}")

            result = tool.execute(**args)
            result_str = str(result)

            # side-channel:供编排器提取(编排器拿不到 _conversation_history 里的 args)
            self._tool_calls_log.append({
                "name": tool_name,
                "args": args,
                "result": result_str,
            })

            # 记录工具调用
            tool_entry_id = str(uuid.uuid4())
            self._safe_append(ToolEntry(
                id=tool_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=parent_id,
                tool_name=tool_name,
                arguments=args,
                result=result_str,
                success=True,
            ))

            # 触发 tool.complete 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.complete", tool_name, success=True)
                except Exception as e:
                    logger.warning(f"tool_progress_callback error: {e}")

            return result_str
        except Exception as e:
            error_msg = f"错误: 工具执行失败: {e}"

            # 记录工具调用失败
            tool_entry_id = str(uuid.uuid4())
            self._safe_append(ToolEntry(
                id=tool_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=parent_id,
                tool_name=tool_name,
                arguments=args,
                result="",
                success=False,
                error=error_msg,
            ))

            # 触发 tool.error 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.error", tool_name, error_code="EXECUTION_ERROR", error_message=str(e))
                except Exception as cb_e:
                    logger.warning(f"tool_progress_callback error: {cb_e}")

            return error_msg

    def _execute_tool_call_from_result(self, tool_call_info: ToolCallResult, parent_id: str) -> str:
        """执行工具调用（Native Function Calling 模式）

        Args:
            tool_call_info: 结构化工具调用信息
            parent_id: 父节点 ID（LLMEntry 的 id）

        Returns:
            工具执行结果字符串
        """
        session_id = self._get_or_create_session_id()
        model = self.config.llm.model
        provider = self.config.llm.provider

        tool = self.tool_registry.get_tool(tool_call_info.tool_name)
        if not tool:
            error_msg = f"错误: 未知工具: {tool_call_info.tool_name}"
            tool_entry_id = str(uuid.uuid4())
            self._safe_append(ToolEntry(
                id=tool_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=parent_id,
                tool_name=tool_call_info.tool_name,
                tool_call_id=tool_call_info.tool_call_id,
                arguments=tool_call_info.arguments,
                result="",
                success=False,
                error=error_msg,
            ))
            return error_msg

        try:
            # 触发 tool.started 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.started", tool_call_info.tool_name)
                except Exception as e:
                    logger.warning(f"tool_progress_callback error: {e}")

            result = tool.execute(**tool_call_info.arguments)
            result_str = str(result)

            # side-channel:供编排器提取
            self._tool_calls_log.append({
                "name": tool_call_info.tool_name,
                "args": dict(tool_call_info.arguments),
                "result": result_str,
            })

            # 记录工具调用
            tool_entry_id = str(uuid.uuid4())
            self._safe_append(ToolEntry(
                id=tool_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=parent_id,
                tool_name=tool_call_info.tool_name,
                tool_call_id=tool_call_info.tool_call_id,
                arguments=tool_call_info.arguments,
                result=result_str,
                success=True,
            ))

            # 触发 tool.complete 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.complete", tool_call_info.tool_name, success=True)
                except Exception as e:
                    logger.warning(f"tool_progress_callback error: {e}")

            return result_str
        except Exception as e:
            error_msg = f"错误: 工具执行失败: {e}"

            # 记录工具调用失败
            tool_entry_id = str(uuid.uuid4())
            self._safe_append(ToolEntry(
                id=tool_entry_id,
                timestamp=time.time(),
                session_id=session_id,
                model=model,
                provider=provider,
                turn_id=self._current_iteration,
                parent_id=parent_id,
                tool_name=tool_call_info.tool_name,
                tool_call_id=tool_call_info.tool_call_id,
                arguments=tool_call_info.arguments,
                result="",
                success=False,
                error=error_msg,
            ))

            # 触发 tool.error 回调
            if self._tool_progress_callback:
                try:
                    self._tool_progress_callback("tool.error", tool_call_info.tool_name, error_code="EXECUTION_ERROR", error_message=str(e))
                except Exception as cb_e:
                    logger.warning(f"tool_progress_callback error: {cb_e}")

            return error_msg

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


class TimeoutTracker:
    """LLM 调用超时追踪器

    使用 threading.Timer 在外部计时，触发超时回调。
    """

    def __init__(self, timeout: float, callback: Callable[[], None]):
        """初始化超时追踪器

        Args:
            timeout: 超时时间（秒）
            callback: 超时触发的回调函数
        """
        self._timeout = timeout
        self._callback = callback
        self._timer: Optional[threading.Timer] = None

    def start(self) -> None:
        """启动计时器"""
        self._timer = threading.Timer(self._timeout, self._callback)
        self._timer.start()

    def cancel(self) -> None:
        """取消计时器"""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


class LLMClient:
    """LLM client with multi-provider support"""

    _ADAPTERS = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
        "gemini": GeminiAdapter,
        "openrouter": OpenRouterAdapter,
        "azure": AzureOpenAIAdapter,
        "ollama": OllamaAdapter,
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
        tools: Optional[list[dict]] = None,
    ) -> str:
        """Call the LLM using the configured provider adapter

        Args:
            system_prompt: System prompt for the conversation
            conversation_history: List of message dicts with 'role' and 'content'
            tools: Optional list of tool definitions in OpenAI function format

        Returns:
            LLM response text
        """
        return self._adapter.complete(system_prompt, conversation_history, tools=tools)


class RateLimitError(Exception):
    """速率限制错误"""
    pass


class ServiceUnavailableError(Exception):
    """服务不可用错误"""
    pass


class MaxRetriesExceededError(Exception):
    """超过最大重试次数"""
    pass
