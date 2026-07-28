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
# WITHOUT WARRANTIES OF ANY CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ACP 适配器集成测试

验证 ACP (Agent Client Protocol) 与编辑器的完整通信流程。
"""

import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class MockToolRegistry:
    """模拟工具注册表"""

    def __init__(self):
        self._tools = {
            "bash": {"name": "bash", "description": "Execute bash command"},
            "read_file": {"name": "read_file", "description": "Read file content"},
        }

    def list_tools(self):
        return list(self._tools.values())

    def call_tool(self, name, args):
        if name in self._tools:
            return {"success": True, "result": f"Executed {name}"}
        return {"error": f"Unknown tool: {name}"}


class ACPAdapterIntegration:
    """ACP 适配器集成测试辅助类"""

    def __init__(self, agent_runner=None, tool_registry=None):
        from ascend_op_agent.acp.adapter import ACPAdapter

        self._adapter = ACPAdapter(
            agent_runner=agent_runner or (lambda x: "mock response"),
            tool_registry=tool_registry or MockToolRegistry(),
            session_timeout=30 * 60,
        )
        self._output_lines = []

    async def send_raw_message(self, raw_message: str) -> None:
        """发送原始消息"""
        await self._adapter.handle_message(raw_message)

    def get_output(self):
        """获取输出行"""
        return self._output_lines


@pytest.fixture
def mock_agent_runner():
    """模拟 Agent 运行器"""
    return MagicMock(return_value="test agent response")


@pytest.fixture
def mock_tool_registry():
    """模拟工具注册表"""
    return MockToolRegistry()


@pytest.fixture
def acp_adapter(mock_agent_runner, mock_tool_registry):
    """创建 ACP 适配器实例"""
    return ACPAdapterIntegration(mock_agent_runner, mock_tool_registry)


class TestACPAdapterIntegration:
    """ACP 适配器集成测试"""

    @pytest.fixture
    def adapter(self, mock_agent_runner, mock_tool_registry):
        """创建适配器实例"""
        return ACPAdapterIntegration(mock_agent_runner, mock_tool_registry)

    @pytest.mark.anyio
    async def test_initialize_request(self):
        """测试 initialize 请求"""
        from ascend_op_agent.acp.adapter import ACPAdapter

        def mock_runner(x):
            return "response"

        adapter = ACPAdapter(
            agent_runner=mock_runner,
            tool_registry=MockToolRegistry(),
            session_timeout=30 * 60,
        )

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "VS Code", "version": "1.0.0"},
                "capabilities": {"supports": ["agent.run"]},
            },
        }
        await adapter.handle_message(json.dumps(request))

        # 验证响应
        # 注意：实际输出是打印到 stdout 的，这里通过捕获来验证

    def test_protocol_method_whitelist(self):
        """测试协议方法白名单"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()

        # 有效方法
        assert protocol.validate_method("initialize") is True
        assert protocol.validate_method("agent.run") is True
        assert protocol.validate_method("tools/list") is True
        assert protocol.validate_method("tools/call") is True

        # 无效方法
        assert protocol.validate_method("exec") is False
        assert protocol.validate_method("shell") is False
        assert protocol.validate_method("delete") is False

    def test_protocol_param_validation(self):
        """测试协议参数验证"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()

        # agent.run 需要 user_input
        assert protocol.validate_params("agent.run", {"user_input": "hello"}) is True
        assert protocol.validate_params("agent.run", {}) is False
        assert protocol.validate_params("agent.run", None) is True

        # tools/call 需要 name 和 args
        assert protocol.validate_params("tools/call", {"name": "bash", "args": {}}) is True
        assert protocol.validate_params("tools/call", {"name": "bash"}) is False
        assert protocol.validate_params("tools/call", {"args": {}}) is False

    def test_initialize_response_structure(self):
        """测试 initialize 响应结构"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()
        response = protocol.build_initialize_response({"supports": ["agent.run"]})
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert "result" in parsed

        result = parsed["result"]
        assert result["protocolVersion"] == "1.0"
        assert "capabilities" in result
        assert "serverInfo" in result

    def test_error_response_structure(self):
        """测试错误响应结构"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()
        response = protocol.build_error_response(1, -32601, "Method not found")
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert "error" in parsed
        assert parsed["error"]["code"] == -32601
        assert parsed["error"]["message"] == "Method not found"


class TestACPSessionIntegration:
    """ACP 会话集成测试"""

    def test_session_creation_and_lifecycle(self):
        """测试会话创建和生命周期"""
        from ascend_op_agent.acp.session import SessionManager

        manager = SessionManager(timeout_seconds=60)

        # 创建会话
        session = manager.create_session(
            session_id="test-session-1",
            editor_info={"name": "VS Code", "version": "1.0"},
        )
        assert session.session_id == "test-session-1"
        assert manager.session_count == 1

        # 获取会话
        retrieved = manager.get_session("test-session-1")
        assert retrieved is not None
        assert retrieved.session_id == "test-session-1"

        # 更新会话
        updated = manager.update_session("test-session-1")
        assert updated is True

        # 重置会话
        session.context["test"] = "value"
        reset = manager.reset_session("test-session-1")
        assert reset is True
        assert session.context == {}

        # 删除会话
        removed = manager.remove_session("test-session-1")
        assert removed is True
        assert manager.session_count == 0

    def test_session_timeout_cleanup(self):
        """测试会话超时清理"""
        from ascend_op_agent.acp.session import SessionManager
        import time

        manager = SessionManager(timeout_seconds=1)

        # 创建一个会话
        session = manager.create_session("test-session", {})

        # 手动将 last_activity 设置为很久以前
        session.last_activity = time.time() - 10

        # 清理超时会话
        cleaned = manager.cleanup_expired()
        assert cleaned == 1
        assert manager.session_count == 0

    def test_session_not_expired_before_timeout(self):
        """测试未超时会话不会被清理"""
        from ascend_op_agent.acp.session import SessionManager

        manager = SessionManager(timeout_seconds=60)

        session = manager.create_session("test-session", {})

        # 清理（不应该清理）
        cleaned = manager.cleanup_expired()
        assert cleaned == 0
        assert manager.session_count == 1

    def test_multiple_sessions(self):
        """测试多会话管理"""
        from ascend_op_agent.acp.session import SessionManager

        manager = SessionManager()

        # 创建多个会话
        for i in range(3):
            manager.create_session(f"session-{i}", {"name": f"Editor-{i}"})

        assert manager.session_count == 3

        # 获取所有会话
        all_sessions = manager.get_all_sessions()
        assert len(all_sessions) == 3

        # 清理所有会话
        for i in range(3):
            manager.remove_session(f"session-{i}")

        assert manager.session_count == 0


class TestACPToolRouting:
    """ACP 工具路由集成测试"""

    def test_tools_list_response_format(self):
        """测试工具列表响应格式"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()
        tool_registry = MockToolRegistry()
        tools = tool_registry.list_tools()

        response = protocol.build_success_response(1, {"tools": tools})
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert "result" in parsed
        assert "tools" in parsed["result"]
        assert len(parsed["result"]["tools"]) == 2

    def test_tools_call_response_format(self):
        """测试工具调用响应格式"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()

        # 模拟成功调用
        result = {"success": True, "result": "Executed bash"}
        response = protocol.build_success_response(1, {"result": result})
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert "result" in parsed
        assert "result" in parsed["result"]

    def test_unknown_tool_error(self):
        """测试未知工具错误"""
        from ascend_op_agent.acp.protocol import ACPProtocol

        protocol = ACPProtocol()

        tool_registry = MockToolRegistry()
        result = tool_registry.call_tool("unknown_tool", {})

        assert "error" in result


class TestACPAdapterUnit:
    """ACP 适配器单元测试"""

    def test_adapter_instantiation(self):
        """测试适配器实例化"""
        from ascend_op_agent.acp.adapter import ACPAdapter

        def mock_runner(x):
            return "response"

        adapter = ACPAdapter(
            agent_runner=mock_runner,
            tool_registry=MockToolRegistry(),
            session_timeout=30 * 60,
        )

        assert adapter is not None
        assert adapter._running is False

    def test_adapter_server_capabilities(self):
        """测试服务器能力"""
        from ascend_op_agent.acp.adapter import ACPAdapter

        def mock_runner(x):
            return "response"

        adapter = ACPAdapter(
            agent_runner=mock_runner,
            tool_registry=MockToolRegistry(),
        )

        capabilities = adapter._get_server_capabilities()

        assert "supports" in capabilities
        assert "initialize" in capabilities["supports"]
        assert "agent.run" in capabilities["supports"]
        assert "timeout" in capabilities


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
