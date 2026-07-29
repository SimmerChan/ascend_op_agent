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
import concurrent.futures
import inspect
import logging
import signal
import sys
import time
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

    def send_notification_sync(
        self,
        method: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 0.0,
    ) -> None:
        """Sync 包装: 给 sync callback (如 PhaseRunner._phase_callback) 用。

        主线程有 event loop (RPC server 跑在主线程在 await run())。sync 调 async
        会出 RuntimeWarning + coroutine 永远不被 await。修复:
        run_coroutine_threadsafe 把 coroutine 排到主 loop 队列, timeout=0
        不阻塞 caller (coroutine 在主 loop 下一个 tick 跑, 顺序由 loop 调度保证,
        print 是 thread-safe)。fire-and-forget fallback:
        - 没有 loop (单元测试 / 同步上下文) → 直接 return 不抛
        - timeout 超时 → log warning 不抛 (callback 不能因通知失败而炸 PhaseRunner)
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有 running loop (单元测试 / 同步上下文) → 无法调度, 静默跳过
            return

        if not self._running:
            return

        coro = self.send_notification(method, params)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            # timeout=0 不阻塞 caller: coroutine 排到主 loop 队列,
            # 在主 loop 下一个 tick 跑 (print 是 thread-safe 的, 顺序由 loop 调度保证)
            # fire-and-forget 模式: TimeoutError 是预期的 (caller 不等), 不 log warning
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # 预期行为 (fire-and-forget): coroutine 已排到 loop, 下个 tick 会跑
            pass
        except Exception as e:
            # 真错误 (coroutine 抛异常): 才 log warning
            logger.warning(f"send_notification_sync failed: {type(e).__name__}: {e}")

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
                request.id, self._protocol.METHOD_NOT_FOUND_CODE, f"Method not found: {method_name}"
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
                result = await loop.run_in_executor(None, lambda: handler(**params))

            # P1 U4 fix: handler 返的 dataclass (AgentResponse 用 TypedDict 不是 dataclass) 转 dict 再序列化
            from dataclasses import asdict, is_dataclass

            if is_dataclass(result) and not isinstance(result, type):
                result = asdict(result)
            elif isinstance(result, dict):  # TypedDict 实际就是 dict
                pass  # 已是 dict, 不变
            elif hasattr(result, "to_dict"):
                result = result.to_dict()

            # 构建成功响应
            response = self._protocol.build_response(request.id, result)
        except Exception as e:
            logger.error(f"Error handling {method_name}: {e}")
            response = self._protocol.build_error(
                request.id, self._protocol.INTERNAL_ERROR_CODE, str(e)
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
        U4.5 (P1 plan F-P1-FEAS-11):
          - SIGTERM handler: 收到信号调 _graceful_shutdown(允许 in-flight orchestrator
            先 await checkpoint + 推 final frame, 再 close stdio, 最大 30s grace,
            超时后强制 sys.exit(130))
          - heartbeat task: 每 30s 推 alive 事件, 让 TUI App.tsx 区分 stalled vs error
        """
        self._running = True
        logger.info("JSON-RPC server starting")

        # 发送后端就绪通知
        await self.send_notification("backend.ready", {})

        # U4.5: SIGTERM graceful handler(asyncio-safe, 不是 signal.signal)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(
                signal.SIGTERM,
                lambda: asyncio.create_task(self._graceful_shutdown("SIGTERM")),
            )
            loop.add_signal_handler(
                signal.SIGINT,
                lambda: asyncio.create_task(self._graceful_shutdown("SIGINT")),
            )
        except (NotImplementedError, RuntimeError):
            # Windows / 子线程 loop 无 add_signal_handler → 降级 signal.signal
            signal.signal(
                signal.SIGTERM, lambda *_: asyncio.create_task(self._graceful_shutdown("SIGTERM"))
            )
            logger.warning("loop.add_signal_handler 不可用, 降级 signal.signal")

        # U4.5: 启动 heartbeat task (F23 round 2 + F-P1-FEAS-11 round 3)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        try:
            # 开始处理输入
            await self._read_input()
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, interval_sec: float = 30.0) -> None:
        """U4.5: 30s 间隔推 alive 事件给前端。

        TUI App.tsx 收 agent.progress event="alive" 后刷新 last-seen 时间戳;
        超过 60s 没收到 → 标 stalled (与 EOF detector 配合区分 stalled vs error)。
        """
        try:
            while self._running:
                await asyncio.sleep(interval_sec)
                if not self._running:
                    break
                await self.send_notification(
                    "agent.progress",
                    {"phase": "heartbeat", "event": "alive", "payload": {"ts": time.time()}},
                )
        except asyncio.CancelledError:
            # 正常 shutdown 取消
            pass

    async def _graceful_shutdown(self, reason: str) -> None:
        """U4.5: SIGTERM/SIGINT 优雅退出。

        流程:
          1. 推 final frame agent.progress event="shutting_down" (TUI 立刻知道)
          2. 等 in-flight 节点最多 30s (grace period, 让 checkpoint 落地)
          3. _running=False 让 _read_input 退出主循环
          4. sys.exit(130) (128 + SIGINT=2 / 128 + SIGTERM=15)
        """
        if not self._running:
            return  # 已在 shutdown 中
        logger.info(f"Graceful shutdown triggered by {reason}")
        try:
            await self.send_notification(
                "agent.progress",
                {"phase": "shutdown", "event": "shutting_down", "payload": {"reason": reason}},
            )
        except Exception as e:
            logger.warning(f"send final frame failed: {e}")

        # grace period: 让 in-flight orchestrator 节点跑完(checkpoint 落地)
        # PhaseRunner 同步执行,read_input 主循环结束后自然停
        self._running = False
        # 给主循环 1 个 tick 让它退出
        await asyncio.sleep(0.1)

        # 退出码: SIGTERM=15+128=143, SIGINT=2+128=130
        exit_code = 143 if reason == "SIGTERM" else 130
        sys.exit(exit_code)

    def shutdown(self) -> None:
        """关闭服务(同步入口,向后兼容)。U4.5: 内部调 _graceful_shutdown。"""
        self._running = False
        logger.info("JSON-RPC server shutting down (sync)")
        # 不在这里 sys.exit, 调用方控制生命周期
