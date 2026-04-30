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

"""MCP集成测试

测试MCP服务器集成相关功能。
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestMCPServerConfig:
    """MCP服务器配置测试"""

    def test_stdio_config_parsing(self):
        """测试stdio配置解析"""
        from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

        config = MCPServerConfig(
            name="code-search",
            type=TransportType.STDIO,
            command="npx /path/to/server",
        )

        assert config.name == "code-search"
        assert config.type == TransportType.STDIO
        assert config.command == "npx /path/to/server"

    def test_http_config_parsing(self):
        """测试HTTP配置解析"""
        from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

        config = MCPServerConfig(
            name="github",
            type=TransportType.HTTP,
            url="https://api.github.com/mcp",
            token="${GITHUB_TOKEN}",
        )

        assert config.type == TransportType.HTTP
        assert "${GITHUB_TOKEN}" in config.token

    def test_streamable_http_config(self):
        """测试streamable-http配置"""
        from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

        config = MCPServerConfig(
            name="custom",
            type=TransportType.STREAMABLE_HTTP,
            url="http://localhost:8080",
        )

        assert config.type == TransportType.STREAMABLE_HTTP

    def test_config_from_dict(self):
        """测试从字典创建配置"""
        from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

        data = {
            "name": "test-server",
            "type": "stdio",
            "command": "npx test",
        }

        config = MCPServerConfig.from_dict(data)

        assert config.name == "test-server"
        assert config.type == TransportType.STDIO


class TestMCPLifecycleManager:
    """MCP生命周期管理器测试"""

    def test_lifecycle_initialization(self):
        """测试生命周期管理器初始化"""
        from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager

        manager = MCPLifecycleManager()

        assert len(manager.processes) == 0
        assert len(manager.health_checks) == 0

    def test_stop_server(self):
        """测试停止服务器"""
        from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager

        manager = MCPLifecycleManager()

        # 模拟添加进程
        mock_process = MagicMock()
        manager.processes["test-server"] = mock_process

        # 停止服务器
        manager.stop_server("test-server")

        # 验证进程被终止
        mock_process.terminate.assert_called_once()
        assert "test-server" not in manager.processes

    def test_cleanup_all_servers(self):
        """测试清理所有服务器"""
        from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager

        manager = MCPLifecycleManager()

        # 模拟多个进程
        for i in range(3):
            mock_process = MagicMock()
            manager.processes[f"server-{i}"] = mock_process

        # 清理所有
        manager.cleanup()

        # 验证所有进程都被终止
        assert len(manager.processes) == 0


class TestMCPToolIntegration:
    """MCP工具集成测试"""

    def test_tool_registration(self):
        """测试工具注册"""
        from ascend_op_agent.agent import ToolRegistry

        registry = ToolRegistry()

        def mock_mcp_tool(arg: str) -> str:
            return f"mcp: {arg}"

        registry.register(
            name="mcp_tool",
            description="Mock MCP tool",
            func=mock_mcp_tool,
        )

        tool = registry.get_tool("mcp_tool")
        assert tool is not None
        assert tool.name == "mcp_tool"

    def test_tool_execution(self):
        """测试工具执行"""
        from ascend_op_agent.agent import Tool

        def mock_mcp_tool(x: int, y: int) -> int:
            return x + y

        tool = Tool(
            name="add",
            description="Add two numbers",
            func=mock_mcp_tool,
        )

        result = tool.execute(x=1, y=2)
        assert result == 3

    def test_tools_list(self):
        """测试工具列表"""
        from ascend_op_agent.agent import ToolRegistry

        registry = ToolRegistry()

        def tool1():
            pass

        def tool2():
            pass

        registry.register(name="tool1", description="Tool 1", func=tool1)
        registry.register(name="tool2", description="Tool 2", func=tool2)

        tools = registry.list_tools()

        assert "tool1" in tools
        assert "tool2" in tools
