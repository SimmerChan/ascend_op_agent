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
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypedDict, Optional

if TYPE_CHECKING:
    from ascend_op_agent.agent.core import AIAgent


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

    def __init__(self, agent: "AIAgent", max_workers: int = 4):
        """初始化异步封装器

        Args:
            agent: AIAgent 实例
            max_workers: 线程池最大工作线程数
        """
        self.agent = agent
        self._thread_pool = ThreadPoolExecutor(max_workers=max_workers)

    async def run_conversation_async(self, user_input: str) -> AgentResponse:
        """异步运行对话

        Args:
            user_input: 用户输入

        Returns:
            AgentResponse 响应对象
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self._thread_pool,
            self.agent.run_conversation,
            user_input
        )
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
