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

"""JSON-RPC 2.0 服务端实现

使用 asyncio 处理异步任务，通过 stdin/stdout 进行通信。
"""

import asyncio
import inspect
import logging
import sys
from typing import Any, Callable, Dict, Optional

from .protocol import (
    JSONRPCProtocol,
    JSONRPCParseError,
    RPCRequest,
    RPCNotification,
)

logger = logging.getLogger(__name__)


class JSONRPCServer:
    """JSON-RPC 2.0 服务端

    使用 asyncio 处理异步任务，通过 stdin/stdout 进行通信。
    """

    def __init__(self):
        self._methods: Dict[str, Callable] = {}
        self._protocol = JSONRPCProtocol()
        self._running = False
        self._output_lock: asyncio.Lock | None = None

    def register_method(self, name: str, handler: Callable) -> None:
        """注册 RPC 处理方法

        Args:
            name: 方法名
            handler: 处理函数，可以是同步或异步函数
        """
        self._methods[name] = handler
        logger.debug(f"Registered method: {name}")

    async def send_notification(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        """发送通知到前端

        Args:
            method: 方法名
            params: 参数
        """
        if not self._running:
            return

        message = self._protocol.build_notification(method, params)
        if self._output_lock is None:
            self._output_lock = asyncio.Lock()
        async with self._output_lock:
            print(message, flush=True)

    async def _handle_message(self, raw_message: str) -> None:
        """处理收到的消息

        Args:
            raw_message: JSON 字符串
        """
        try:
            rpc_msg = self._protocol.parse_request(raw_message)
        except JSONRPCParseError as e:
            logger.error(f"Parse error: {e.message}")
            # Notification 错误不返回响应
            return

        # 处理 Notification
        if isinstance(rpc_msg, RPCNotification):
            await self._handle_notification(rpc_msg)
        # 处理 Request
        else:
            await self._handle_request(rpc_msg)

    async def _handle_notification(self, notification: RPCNotification) -> None:
        """处理通知（暂不支持前端通知）"""
        logger.debug(f"Received notification: {notification.method}")

    async def _ensure_lock(self) -> asyncio.Lock:
        """确保锁已初始化"""
        if self._output_lock is None:
            self._output_lock = asyncio.Lock()
        return self._output_lock

    async def _handle_request(self, request: RPCRequest) -> None:
        """处理请求

        Args:
            request: RPC 请求对象
        """
        method_name = request.method
        handler = self._methods.get(method_name)

        if handler is None:
            error_msg = self._protocol.build_error(
                request.id,
                self._protocol.METHOD_NOT_FOUND_CODE,
                f"Method not found: {method_name}"
            )
            lock = await self._ensure_lock()
            async with lock:
                print(error_msg, flush=True)
            return

        # 调用处理函数
        try:
            params = request.params or {}
            if inspect.iscoroutinefunction(handler):
                result = await handler(**params)
            else:
                # 同步函数：在线程池中执行
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: handler(**params)
                )

            # 构建成功响应
            response = self._protocol.build_response(request.id, result)
        except Exception as e:
            logger.error(f"Error handling {method_name}: {e}")
            response = self._protocol.build_error(
                request.id,
                self._protocol.INTERNAL_ERROR_CODE,
                str(e)
            )

        lock = await self._ensure_lock()
        async with lock:
            print(response, flush=True)

    async def _read_input(self) -> None:
        """读取 stdin 输入"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    # EOF
                    logger.info("stdin closed, shutting down")
                    break

                raw_message = line.decode("utf-8").strip()
                if raw_message:
                    await self._handle_message(raw_message)

            except Exception as e:
                logger.error(f"Error reading input: {e}")

    async def run(self) -> None:
        """启动 RPC 服务

        运行就绪后发送 backend.ready 通知。
        """
        self._running = True
        logger.info("JSON-RPC server starting")

        # 发送后端就绪通知
        await self.send_notification("backend.ready", {})

        # 开始处理输入
        await self._read_input()

    def shutdown(self) -> None:
        """关闭服务"""
        self._running = False
        logger.info("JSON-RPC server shutting down")
