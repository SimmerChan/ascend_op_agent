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

"""NotificationQueue - 线程安全的同步→异步桥接

使用 queue.Queue 作为同步→异步桥接。worker 线程放入事件，
async 任务消费并调用 send_notification。
"""

import asyncio
import logging
import queue
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class NotificationQueue:
    """线程安全的通知队列，用于同步→异步桥接

    Worker 线程调用 put() 放入事件，async 任务通过 start_consuming()
    开始消费并调用 send_notification。
    """

    def __init__(self, maxsize: int = 100):
        """初始化通知队列

        Args:
            maxsize: 队列最大容量，0 表示无限制
        """
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._running = False
        self._consume_task: Optional[asyncio.Task[Any]] = None

    def put(self, event_type: str, params: dict[str, Any]) -> None:
        """放入通知事件（从 worker 线程调用）

        Args:
            event_type: 事件类型
            params: 事件参数
        """
        self._queue.put({"type": event_type, "params": params})

    def start_consuming(
        self,
        loop: asyncio.AbstractEventLoop,
        send_notification_fn: Callable[[str, dict[str, Any]], asyncio.Future],
    ) -> None:
        """开始消费队列中的事件（从 async 上下文调用）

        Args:
            loop: 事件循环
            send_notification_fn: 发送通知的 async 函数
        """
        if self._running:
            logger.warning("NotificationQueue already running")
            return

        self._running = True
        self._consume_task = asyncio.create_task(self._consume_loop(loop, send_notification_fn))

    async def _consume_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        send_notification_fn: Callable[[str, dict[str, Any]], asyncio.Future],
    ) -> None:
        """消费循环，从队列取事件并调用 send_notification

        Args:
            loop: 事件循环
            send_notification_fn: 发送通知的 async 函数
        """
        while self._running:
            try:
                event = await loop.run_in_executor(None, self._queue.get)
                try:
                    await send_notification_fn(event["type"], event["params"])
                except Exception as e:
                    logger.warning(f"send_notification error: {e}")
            except Exception as e:
                if self._running:
                    logger.warning(f"consume_loop error: {e}")

    def stop(self) -> None:
        """停止消费"""
        self._running = False
        if self._consume_task:
            self._consume_task.cancel()
            self._consume_task = None
