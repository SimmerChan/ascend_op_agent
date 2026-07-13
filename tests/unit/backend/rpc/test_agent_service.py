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

"""Agent Service 单元测试"""

import pytest

from ascend_op_agent.backend.rpc.agent_service import (
    AgentResponse,
    AgentAsyncWrapper,
)


class MockAgent:
    """模拟 AIAgent 用于测试"""

    def __init__(self):
        self.call_count = 0
        self.reset_called = False

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override=None,
        *,
        task_type=None,
    ) -> str:
        """模拟对话"""
        self.call_count += 1
        return f"Mock response to: {user_input}"

    def reset_conversation(self) -> None:
        """模拟重置"""
        self.reset_called = True


class TestAgentResponse:
    """AgentResponse 类型测试"""

    def test_agent_response_structure(self):
        """测试 AgentResponse 结构"""
        response: AgentResponse = {
            "status": "completed",
            "response": "test response",
            "data": None
        }

        assert response["status"] == "completed"
        assert response["response"] == "test response"
        assert response["data"] is None

    def test_agent_response_with_data(self):
        """测试带数据的响应"""
        response: AgentResponse = {
            "status": "waiting_confirmation",
            "response": None,
            "data": {"type": "confirm", "options": ["yes", "no"]}
        }

        assert response["status"] == "waiting_confirmation"
        assert response["data"]["type"] == "confirm"


class TestAgentAsyncWrapper:
    """AgentAsyncWrapper 测试"""

    @pytest.fixture
    def mock_agent(self):
        """创建模拟 Agent"""
        return MockAgent()

    @pytest.mark.asyncio
    async def test_run_conversation_async(self, mock_agent):
        """测试异步运行对话"""
        wrapper = AgentAsyncWrapper(mock_agent)
        result = await wrapper.run_conversation_async("hello world")

        assert result["status"] == "completed"
        assert result["response"] == "Mock response to: hello world"
        assert mock_agent.call_count == 1

    def test_run_conversation_sync(self, mock_agent):
        """测试同步运行对话"""
        wrapper = AgentAsyncWrapper(mock_agent)
        result = wrapper.run_conversation("sync input")

        assert result == "Mock response to: sync input"
        assert mock_agent.call_count == 1

    def test_multiple_concurrent_calls(self, mock_agent):
        """测试多次并发调用"""
        import asyncio

        wrapper = AgentAsyncWrapper(mock_agent)

        async def run_test():
            results = await asyncio.gather(
                wrapper.run_conversation_async("input1"),
                wrapper.run_conversation_async("input2"),
                wrapper.run_conversation_async("input3"),
            )
            return results

        results = asyncio.run(run_test())

        assert len(results) == 3
        assert all(r["status"] == "completed" for r in results)
        assert mock_agent.call_count == 3

    def test_shutdown(self, mock_agent):
        """测试关闭线程池"""
        wrapper = AgentAsyncWrapper(mock_agent)
        wrapper.shutdown()

        # 验证线程池已关闭（后续提交任务会失败）
        # 这里只验证不抛出异常
        assert True
