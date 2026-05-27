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

"""Agent 服务封装模块

将同步 AIAgent 封装为异步接口，提供线程池执行。
"""

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Callable, TypedDict, Optional

from .notification_queue import NotificationQueue

if TYPE_CHECKING:
    from ascend_op_agent.agent.core import AIAgent

logger = logging.getLogger(__name__)


class AgentResponse(TypedDict):
    """Agent 响应的类型定义"""
    status: str
    response: Optional[str]
    data: Optional[dict]


class AgentAsyncWrapper:
    """将同步 AIAgent 封装为异步接口

    使用 ThreadPoolExecutor 将同步的 AIAgent.run_conversation()
    封装为异步接口，避免阻塞 RPC 服务。
    """

    def __init__(
        self,
        agent: "AIAgent",
        max_workers: int = 4,
        send_notification_fn: Optional[Callable[[str, dict[str, Any]], asyncio.Future]] = None,
    ):
        """初始化异步封装器

        Args:
            agent: AIAgent 实例
            max_workers: 线程池最大工作线程数
            send_notification_fn: 发送通知的 async 函数
        """
        self.agent = agent
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._send_notification_fn = send_notification_fn
        self._notification_queue: Optional[NotificationQueue] = None

        # 如果提供了 send_notification_fn，创建通知队列和回调
        if send_notification_fn is not None:
            self._notification_queue = NotificationQueue()

            # 创建包装函数，将事件放入队列
            def tool_progress_callback(event_type: str, tool_name: str, **kwargs: Any) -> None:
                params: dict[str, Any] = {"tool_name": tool_name}
                params.update(kwargs)
                # 映射事件类型到前端 stage
                if event_type == "tool.started":
                    params["stage"] = "tool_executing"
                elif event_type == "tool.complete":
                    params["stage"] = "tool_executing"
                    params["success"] = True
                elif event_type == "tool.error":
                    params["stage"] = "tool_executing"
                self._notification_queue.put("agent.progress", params)

            def status_callback(kind: str, text: Optional[str] = None) -> None:
                params: dict[str, Any] = {}
                if text:
                    params["text"] = text
                if kind == "thinking":
                    params["stage"] = "thinking"
                elif kind == "idle":
                    params["stage"] = "idle"
                elif kind == "completed":
                    params["stage"] = "completed"
                elif kind == "waiting":
                    params["stage"] = "waiting"
                self._notification_queue.put("agent.progress", params)

            # 传递包装后的回调给 AIAgent
            self.agent._tool_progress_callback = tool_progress_callback
            self.agent._status_callback = status_callback

    async def run_conversation_async(self, user_input: str) -> AgentResponse:
        """异步运行对话

        Args:
            user_input: 用户输入

        Returns:
            AgentResponse 响应对象
        """
        loop = asyncio.get_event_loop()

        # 启动通知队列消费
        if self._notification_queue is not None and self._send_notification_fn is not None:
            self._notification_queue.start_consuming(loop, self._send_notification_fn)

        try:
            result = await loop.run_in_executor(
                self._thread_pool,
                self.agent.run_conversation,
                user_input
            )
        finally:
            # 停止通知队列消费
            if self._notification_queue is not None:
                self._notification_queue.stop()

        return AgentResponse(status="completed", response=result, data=None)

    def run_conversation(self, user_input: str) -> str:
        """同步版本，供非异步上下文调用

        Args:
            user_input: 用户输入

        Returns:
            Agent 响应字符串
        """
        return self.agent.run_conversation(user_input)

    def shutdown(self) -> None:
        """关闭线程池"""
        self._thread_pool.shutdown(wait=True)
