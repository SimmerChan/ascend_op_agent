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

"""ACP 适配器主模块

将 ACP 协议、会话管理和工具路由整合在一起，提供完整的 ACP 编辑器集成。
"""

import asyncio
import logging
import sys
import uuid
from typing import Any, Callable, Optional

from .protocol import ACPProtocol
from .session import SessionManager, ACPSession

logger = logging.getLogger(__name__)


class ACPAdapter:
    """ACP 适配器主类

    整合协议处理、会话管理和工具路由，提供完整的 ACP 编辑器集成。
    使用 stdio 与编辑器通信。
    """

    def __init__(
        self,
        agent_runner: Callable[[str], str],
        tool_registry: Optional[Any] = None,
        session_timeout: float = 30 * 60,
    ):
        """初始化 ACP 适配器

        Args:
            agent_runner: Agent 运行回调函数，接受 str 返回 str
            tool_registry: 工具注册表（可选）
            session_timeout: 会话超时时间（秒），默认 30 分钟
        """
        self._protocol = ACPProtocol()
        self._session_manager = SessionManager(timeout_seconds=session_timeout)
        self._agent_runner = agent_runner
        self._tool_registry = tool_registry
        self._running = False
        self._output_lock: asyncio.Lock = asyncio.Lock()
        self._current_session: Optional[ACPSession] = None

    async def send_notification(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        """发送通知到编辑器

        Args:
            method: 方法名
            params: 参数
        """
        if not self._running:
            return
        message = self._protocol.build_notification(method, params)
        async with self._output_lock:
            print(message, flush=True)

    async def handle_message(self, raw_message: str) -> None:
        """处理收到的消息

        Args:
            raw_message: JSON-RPC 消息字符串
        """
        try:
            request, notification = self._protocol.parse_message(raw_message)
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return

        if notification:
            # 处理通知（目前不处理编辑器通知）
            logger.debug(f"Received notification: {notification.method}")
            return

        if request:
            await self._handle_request(request)

    async def _handle_request(self, request) -> None:
        """处理请求

        Args:
            request: RPCRequest 对象
        """
        method = request.method

        # 验证方法白名单
        if not self._protocol.validate_method(method):
            response = self._protocol.build_error_response(
                request.id,
                self._protocol._protocol.METHOD_NOT_FOUND_CODE,
                f"Method not found: {method}"
            )
            async with self._output_lock:
                print(response, flush=True)
            return

        # 验证参数
        if not self._protocol.validate_params(method, request.params):
            response = self._protocol.build_error_response(
                request.id,
                self._protocol._protocol.INVALID_PARAMS_CODE,
                f"Invalid params for method: {method}"
            )
            async with self._output_lock:
                print(response, flush=True)
            return

        # 处理方法
        try:
            if method == ACPProtocol.INITIALIZE:
                result = await self._handle_initialize(request.params, request.id)
            elif method == ACPProtocol.AGENT_RUN:
                result = await self._handle_agent_run(request.params, request.id)
            elif method == ACPProtocol.TOOLS_LIST:
                result = await self._handle_tools_list(request.id)
            elif method == ACPProtocol.TOOLS_CALL:
                result = await self._handle_tools_call(request.params, request.id)
            else:
                result = None

            if result is not None:
                response = self._protocol.build_success_response(request.id, result)
                async with self._output_lock:
                    print(response, flush=True)
        except Exception as e:
            logger.error(f"Error handling {method}: {e}")
            response = self._protocol.build_error_response(
                request.id,
                self._protocol._protocol.INTERNAL_ERROR_CODE,
                str(e)
            )
            async with self._output_lock:
                print(response, flush=True)

    async def _handle_initialize(self, params: Optional[dict[str, Any]], req_id: Any) -> dict[str, Any]:
        """处理 initialize 请求

        Args:
            params: 参数
            req_id: 请求 ID

        Returns:
            初始化结果
        """
        client_info = params.get("clientInfo", {}) if params else {}
        capabilities = params.get("capabilities", {}) if params else {}

        # 创建会话
        session_id = str(uuid.uuid4())
        self._current_session = self._session_manager.create_session(
            session_id=session_id,
            editor_info=client_info,
        )
        if capabilities:
            self._current_session.capabilities = capabilities

        logger.info(f"Session initialized: {session_id}, client: {client_info}")

        return {
            "protocolVersion": "1.0",
            "capabilities": self._get_server_capabilities(),
            "sessionId": session_id,
        }

    async def _handle_agent_run(self, params: Optional[dict[str, Any]], req_id: Any) -> dict[str, Any]:
        """处理 agent.run 请求

        Args:
            params: 参数
            req_id: 请求 ID

        Returns:
            Agent 运行结果
        """
        if not self._current_session:
            raise ValueError("Session not initialized")

        user_input = params.get("user_input", "") if params else ""
        self._current_session.update_activity()

        # 运行 Agent
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            self._agent_runner,
            user_input
        )

        return {"response": response}

    async def _handle_tools_list(self, req_id: Any) -> dict[str, Any]:
        """处理 tools/list 请求

        Args:
            req_id: 请求 ID

        Returns:
            工具列表
        """
        if not self._current_session:
            raise ValueError("Session not initialized")

        self._current_session.update_activity()

        # 获取工具列表
        if self._tool_registry:
            tools = self._tool_registry.list_tools()
        else:
            tools = []

        return {"tools": tools}

    async def _handle_tools_call(self, params: Optional[dict[str, Any]], req_id: Any) -> dict[str, Any]:
        """处理 tools/call 请求

        Args:
            params: 参数
            req_id: 请求 ID

        Returns:
            工具调用结果
        """
        if not self._current_session:
            raise ValueError("Session not initialized")

        tool_name = params.get("name", "") if params else ""
        tool_args = params.get("args", {}) if params else {}
        self._current_session.update_activity()

        # 调用工具
        if self._tool_registry:
            result = self._tool_registry.call_tool(tool_name, tool_args)
        else:
            result = {"error": "Tool registry not available"}

        return {"result": result}

    def _get_server_capabilities(self) -> dict[str, Any]:
        """获取服务器能力

        Returns:
            服务器能力字典
        """
        return {
            "supports": ["initialize", "agent.run", "tools/list", "tools/call"],
            "timeout": int(self._session_manager.timeout_seconds),
        }

    async def run(self) -> None:
        """启动 ACP 适配器

        从 stdin 读取消息，处理后通过 stdout 返回响应。
        """
        self._running = True
        logger.info("ACP adapter starting")

        # 发送就绪通知
        await self.send_notification("backend.ready", {})

        # 读取并处理消息
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    logger.info("stdin closed, shutting down")
                    break

                raw_message = line.decode("utf-8").strip()
                if raw_message:
                    await self.handle_message(raw_message)

            except Exception as e:
                logger.error(f"Error reading input: {e}")

    def shutdown(self) -> None:
        """关闭 ACP 适配器"""
        self._running = False
        self._session_manager.stop_cleanup_task()
        logger.info("ACP adapter shutting down")