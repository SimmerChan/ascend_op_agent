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

"""MCP模块测试"""

import os

import pytest

from ascend_op_agent.mcp.client import MCPClient, MCPClientPool, MCPConnectionError
from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager, MCPProcessError
from ascend_op_agent.mcp.oauth import MCPOAuthManager, MCPOAuthError
from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType


class TestTransportType:
    """TransportType测试"""

    def test_enum_values(self):
        """测试枚举值"""
        assert TransportType.STDIO.value == "stdio"
        assert TransportType.HTTP.value == "http"
        assert TransportType.STREAMABLE_HTTP.value == "streamable-http"


class TestMCPServerConfig:
    """MCPServerConfig测试"""

    def test_from_dict_stdio(self):
        """测试从字典创建 stdio 配置"""
        data = {
            "name": "test-server",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        config = MCPServerConfig.from_dict(data)

        assert config.name == "test-server"
        assert config.type == TransportType.STDIO
        assert config.command == "npx"
        assert config.args == ["-y", "@modelcontextprotocol/server-filesystem"]

    def test_from_dict_http(self):
        """测试从字典创建 HTTP 配置"""
        data = {
            "name": "http-server",
            "type": "http",
            "url": "https://api.example.com/mcp",
            "token": "Bearer token123",
        }
        config = MCPServerConfig.from_dict(data)

        assert config.name == "http-server"
        assert config.type == TransportType.HTTP
        assert config.url == "https://api.example.com/mcp"
        assert config.token == "Bearer token123"

    def test_from_dict_streamable_http(self):
        """测试从字典创建 streamable-http 配置"""
        data = {
            "name": "streamable-server",
            "type": "streamable-http",
            "url": "http://localhost:8080",
        }
        config = MCPServerConfig.from_dict(data)

        assert config.name == "streamable-server"
        assert config.type == TransportType.STREAMABLE_HTTP

    def test_to_dict(self):
        """测试转换为字典"""
        config = MCPServerConfig(
            name="test",
            type=TransportType.STDIO,
            command="npx",
        )
        result = config.to_dict()

        assert result["name"] == "test"
        assert result["type"] == "stdio"
        assert result["command"] == "npx"

    def test_requires_oauth(self):
        """测试 OAuth 检测"""
        config_without_oauth = MCPServerConfig(name="test", type=TransportType.STDIO)
        assert config_without_oauth.requires_oauth() is False

        config_with_oauth = MCPServerConfig(
            name="test",
            type=TransportType.HTTP,
            url="http://localhost",
            oauth={
                "type": "oauth",
                "client_id": "id",
                "client_secret": "secret",
                "token_url": "http://auth",
            },
        )
        assert config_with_oauth.requires_oauth() is True

    def test_get_auth_header_with_env_var(self):
        """测试环境变量引用的认证头"""
        os.environ["TEST_MCP_TOKEN"] = "secret_token_123"

        config = MCPServerConfig(
            name="test",
            type=TransportType.HTTP,
            url="http://localhost",
            token="${TEST_MCP_TOKEN}",
        )
        header = config.get_auth_header()

        assert header == {"Authorization": "Bearer secret_token_123"}

        del os.environ["TEST_MCP_TOKEN"]


class TestMCPLifecycleManager:
    """MCPLifecycleManager测试"""

    def test_creation(self):
        """测试创建"""
        manager = MCPLifecycleManager()
        assert len(manager.processes) == 0

    def test_start_http_server(self):
        """测试启动 HTTP 服务器"""
        manager = MCPLifecycleManager()
        config = MCPServerConfig(
            name="http-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        result = manager.start_server(config)
        assert result is True
        assert "http-test" in manager.processes

        manager.cleanup()

    def test_start_stdio_server_without_command(self):
        """测试 stdio 模式无命令时抛出异常"""
        manager = MCPLifecycleManager()
        config = MCPServerConfig(
            name="stdio-test",
            type=TransportType.STDIO,
        )

        with pytest.raises(ValueError, match="stdio mode requires command"):
            manager.start_server(config)

    def test_stop_nonexistent_server(self):
        """测试停止不存在的服务器"""
        manager = MCPLifecycleManager()
        result = manager.stop_server("nonexistent")
        assert result is True

    def test_health_check_nonexistent(self):
        """测试不存在的服务器健康检查"""
        manager = MCPLifecycleManager()
        result = manager.health_check("nonexistent")
        assert result is False

    def test_get_server_status(self):
        """测试获取服务器状态"""
        manager = MCPLifecycleManager()
        config = MCPServerConfig(
            name="http-status-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        manager.start_server(config)
        status = manager.get_server_status("http-status-test")

        assert status["name"] == "http-status-test"
        assert status["running"] is True

        manager.cleanup()

    def test_cleanup(self):
        """测试清理"""
        manager = MCPLifecycleManager()
        config = MCPServerConfig(
            name="cleanup-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        manager.start_server(config)
        manager.cleanup()

        assert len(manager.processes) == 0

    def test_context_manager(self):
        """测试上下文管理器"""
        with MCPLifecycleManager() as manager:
            config = MCPServerConfig(
                name="context-test",
                type=TransportType.HTTP,
                url="http://localhost:8080",
            )
            manager.start_server(config)

        # 退出时应该自动清理
        assert len(manager.processes) == 0


class TestMCPOAuthManager:
    """MCPOAuthManager测试"""

    def test_creation(self):
        """测试创建"""
        manager = MCPOAuthManager(server_name="test-server")
        assert manager.server_name == "test-server"

    def test_get_env_token(self):
        """测试从环境变量获取 Token"""
        # 注意: server_name 是 "test-server"，转换后是 "TEST-SERVER"
        # 所以环境变量名是 OAUTH_TOKEN_TEST-SERVER
        os.environ["OAUTH_TOKEN_TEST-SERVER"] = "env_token_123"

        manager = MCPOAuthManager(server_name="test-server")
        header = manager.get_auth_header()

        assert header == "Bearer env_token_123"

        del os.environ["OAUTH_TOKEN_TEST-SERVER"]

    def test_get_auth_header_no_oauth(self):
        """测试无 OAuth 配置时"""
        manager = MCPOAuthManager(server_name="test-server")
        header = manager.get_auth_header()
        assert header is None

    def test_clear_token_cache(self):
        """测试清除 Token 缓存"""
        manager = MCPOAuthManager(server_name="test-server")
        manager._access_token = "cached_token"
        manager._token_expires_at = 9999999999

        manager.clear_token_cache()

        assert manager._access_token is None
        assert manager._token_expires_at == 0


class TestMCPClient:
    """MCPClient测试"""

    def test_creation(self):
        """测试创建"""
        config = MCPServerConfig(
            name="test-client",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )
        client = MCPClient(config)

        assert client.config.name == "test-client"
        assert client.is_connected() is False

    def test_connect_disconnect(self):
        """测试连接和断开"""
        config = MCPServerConfig(
            name="connect-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        with MCPLifecycleManager() as lifecycle:
            client = MCPClient(config, lifecycle)
            assert client.connect() is True
            assert client.is_connected() is True

            client.disconnect()
            assert client.is_connected() is False

    def test_send_request_not_connected(self):
        """测试未连接时发送请求"""
        config = MCPServerConfig(
            name="not-connected-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )
        client = MCPClient(config)

        with pytest.raises(MCPConnectionError, match="not connected"):
            client.send_request("test_method")

    def test_context_manager(self):
        """测试上下文管理器"""
        config = MCPServerConfig(
            name="context-client-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        with MCPClient(config) as client:
            assert client.is_connected() is True


class TestMCPClientPool:
    """MCPClientPool测试"""

    def test_creation(self):
        """测试创建"""
        pool = MCPClientPool()
        assert len(pool.clients) == 0

    def test_add_server(self):
        """测试添加服务器"""
        pool = MCPClientPool()
        config = MCPServerConfig(
            name="pool-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        client = pool.add_server(config)
        assert pool.get_client("pool-test") is client
        assert client.config.name == "pool-test"

        pool.cleanup()

    def test_remove_server(self):
        """测试移除服务器"""
        pool = MCPClientPool()
        config = MCPServerConfig(
            name="remove-test",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )

        pool.add_server(config)
        pool.remove_server("remove-test")

        assert pool.get_client("remove-test") is None
        pool.cleanup()

    def test_connect_all(self):
        """测试连接所有服务器"""
        pool = MCPClientPool()

        config1 = MCPServerConfig(
            name="all-test-1",
            type=TransportType.HTTP,
            url="http://localhost:8080",
        )
        config2 = MCPServerConfig(
            name="all-test-2",
            type=TransportType.HTTP,
            url="http://localhost:8081",
        )

        pool.add_server(config1)
        pool.add_server(config2)

        results = pool.connect_all()

        assert results["all-test-1"] is True
        assert results["all-test-2"] is True

        pool.cleanup()

    def test_context_manager(self):
        """测试上下文管理器"""
        with MCPClientPool() as pool:
            config = MCPServerConfig(
                name="pool-context-test",
                type=TransportType.HTTP,
                url="http://localhost:8080",
            )
            pool.add_server(config)
            pool.connect_all()

        # 退出时应该自动清理
        assert len(pool.clients) == 0
