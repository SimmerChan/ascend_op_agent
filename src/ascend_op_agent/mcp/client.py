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

"""MCPClient - MCP客户端

参考 Hermes Agent 实现，支持三种传输模式:
- stdio: 标准输入输出
- http: HTTP 请求响应
- streamable-http: 流式 HTTP
"""

import logging
from typing import Any, Optional

from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager
from ascend_op_agent.mcp.oauth import MCPOAuthManager
from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

logger = logging.getLogger(__name__)


class MCPConnectionError(Exception):
    """MCP 连接错误"""

    pass


class MCPClient:
    """MCP 客户端

    支持三种传输模式的 MCP 服务器连接。
    """

    def __init__(
        self,
        config: MCPServerConfig,
        lifecycle_manager: Optional[MCPLifecycleManager] = None,
    ):
        """
        Args:
            config: MCP 服务器配置
            lifecycle_manager: 生命周期管理器（可选）
        """
        self.config = config
        self.lifecycle = lifecycle_manager or MCPLifecycleManager()
        self.oauth_manager = MCPOAuthManager(
            server_name=config.name,
            oauth_config=config.oauth,
        )

        self._connected = False

    def connect(self) -> bool:
        """连接到 MCP 服务器

        Returns:
            是否连接成功
        """
        try:
            # 获取认证头
            auth_header = self.oauth_manager.get_auth_header()

            # 启动或连接服务器
            success = self.lifecycle.start_server(self.config)

            if success:
                self._connected = True
                logger.info(f"MCP server {self.config.name} connected successfully")
            else:
                logger.warning(f"Failed to connect to MCP server {self.config.name}")

            return success

        except Exception as e:
            logger.error(f"Error connecting to MCP server {self.config.name}: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """断开 MCP 服务器连接"""
        try:
            self.lifecycle.stop_server(self.config.name)
            self._connected = False
            logger.info(f"MCP server {self.config.name} disconnected")
        except Exception as e:
            logger.error(f"Error disconnecting from MCP server {self.config.name}: {e}")

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self._connected and self.lifecycle.is_server_running(self.config.name)

    def send_request(self, method: str, params: Optional[dict] = None) -> Any:
        """发送请求到 MCP 服务器

        Args:
            method: 请求方法
            params: 请求参数

        Returns:
            响应结果

        Raises:
            MCPConnectionError: 连接错误
        """
        if not self.is_connected():
            raise MCPConnectionError(f"MCP server {self.config.name} is not connected")

        if self.config.type == TransportType.STDIO:
            return self._send_stdio_request(method, params)
        elif self.config.type in (TransportType.HTTP, TransportType.STREAMABLE_HTTP):
            return self._send_http_request(method, params)
        else:
            raise MCPConnectionError(f"Unsupported transport type: {self.config.type}")

    def _send_stdio_request(self, method: str, params: Optional[dict]) -> Any:
        """通过 stdio 发送请求"""
        import json

        proc = self.lifecycle.processes.get(self.config.name)
        if not proc:
            raise MCPConnectionError(f"No process for MCP server {self.config.name}")

        # 构建请求
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        try:
            # 发送请求
            request_str = json.dumps(request) + "\n"
            proc.stdin.write(request_str.encode())
            proc.stdin.flush()

            # 读取响应
            import select

            if select.select([proc.stdout], [], [], 30)[0]:
                response_line = proc.stdout.readline()
                if response_line:
                    response = json.loads(response_line.decode())
                    return response.get("result")

            raise MCPConnectionError(f"No response from MCP server {self.config.name}")

        except Exception as e:
            raise MCPConnectionError(f"stdio request failed: {e}")

    def _send_http_request(self, method: str, params: Optional[dict]) -> Any:
        """通过 HTTP 发送请求"""
        import json
        import urllib.request

        if not self.config.url:
            raise MCPConnectionError(f"No URL configured for MCP server {self.config.name}")

        # 构建请求
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        headers = {"Content-Type": "application/json"}

        # 添加认证头
        auth_header = self.oauth_manager.get_auth_header()
        if auth_header:
            headers["Authorization"] = auth_header.replace("Bearer ", "")

        # 添加自定义头
        if self.config.headers:
            headers.update(self.config.headers)

        try:
            req = urllib.request.Request(
                self.config.url,
                data=json.dumps(request).encode(),
                headers=headers,
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode())
                return result.get("result")

        except Exception as e:
            raise MCPConnectionError(f"HTTP request failed: {e}")

    def __enter__(self) -> "MCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()


class MCPClientPool:
    """MCP 客户端池

    管理多个 MCP 服务器连接。
    """

    def __init__(self):
        self.clients: dict[str, MCPClient] = {}
        self.lifecycle = MCPLifecycleManager()

    def add_server(self, config: MCPServerConfig) -> MCPClient:
        """添加 MCP 服务器"""
        if config.name in self.clients:
            self.remove_server(config.name)

        client = MCPClient(config, self.lifecycle)
        self.clients[config.name] = client
        return client

    def remove_server(self, name: str) -> None:
        """移除 MCP 服务器"""
        if name in self.clients:
            self.clients[name].disconnect()
            del self.clients[name]

    def get_client(self, name: str) -> Optional[MCPClient]:
        """获取 MCP 客户端"""
        return self.clients.get(name)

    def connect_all(self) -> dict[str, bool]:
        """连接所有服务器

        Returns:
            每个服务器的连接结果
        """
        results = {}
        for name, client in self.clients.items():
            results[name] = client.connect()
        return results

    def disconnect_all(self) -> None:
        """断开所有服务器"""
        for client in self.clients.values():
            client.disconnect()

    def cleanup(self) -> None:
        """清理所有资源"""
        self.disconnect_all()
        self.lifecycle.cleanup()
        self.clients.clear()

    def __enter__(self) -> "MCPClientPool":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
