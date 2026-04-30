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

"""MCPOAuthManager - MCP OAuth认证管理

参考 Hermes MCPOAuthManager 实现，支持 OAuth 2.0 客户端凭证流。
"""

import os
import re
import time
from typing import Any, Optional


class MCPOAuthError(Exception):
    """OAuth 错误"""
    pass


class MCPOAuthManager:
    """OAuth 2.0 客户端凭证流管理器

    支持:
    - OAuth 2.0 客户端凭证流
    - 环境变量回退
    - Token 缓存和自动刷新
    """

    TOKEN_VAR_PATTERN = re.compile(r'\$\{([^}]+)\}')

    def __init__(self, server_name: str, oauth_config: Optional[dict[str, Any]] = None):
        """
        Args:
            server_name: 服务器名称
            oauth_config: OAuth 配置，包含:
                - type: "oauth"
                - client_id: OAuth 客户端 ID
                - client_secret: OAuth 客户端密钥
                - token_url: Token 获取 URL
        """
        self.server_name = server_name
        self.oauth_config = oauth_config or {}

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    def get_auth_header(self) -> Optional[str]:
        """获取认证头

        Returns:
            格式: "Bearer {token}"
        """
        if self.oauth_config.get("type") == "oauth":
            token = self._get_oauth_token()
            if token:
                return f"Bearer {token}"

        # 回退到环境变量
        token = self._get_env_token()
        if token:
            return f"Bearer {token}"

        return None

    def _resolve_env_var(self, value: str) -> str:
        """解析环境变量引用"""
        if not value:
            return value

        def replace_env_var(match):
            env_var = match.group(1)
            return os.getenv(env_var, "")

        return self.TOKEN_VAR_PATTERN.sub(replace_env_var, value)

    def _get_oauth_token(self) -> Optional[str]:
        """获取 OAuth Token"""
        if not self.oauth_config:
            return None

        # 检查缓存
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        # 获取配置
        client_id = self._resolve_env_var(self.oauth_config.get("client_id", ""))
        client_secret = self._resolve_env_var(self.oauth_config.get("client_secret", ""))
        token_url = self._resolve_env_var(self.oauth_config.get("token_url", ""))

        if not all([client_id, client_secret, token_url]):
            return None

        try:
            # 发起 OAuth 请求
            import urllib.request
            import urllib.parse
            import json

            data = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            }).encode()

            req = urllib.request.Request(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode())

            self._access_token = result.get("access_token")
            expires_in = result.get("expires_in", 3600)
            self._token_expires_at = time.time() + expires_in - 60  # 提前1分钟过期

            return self._access_token

        except Exception as e:
            raise MCPOAuthError(f"Failed to get OAuth token: {e}")

    def _get_env_token(self) -> Optional[str]:
        """从环境变量获取 Token"""
        # 尝试多个可能的环境变量名
        env_vars = [
            f"OAUTH_TOKEN_{self.server_name.upper()}",
            f"MCP_TOKEN_{self.server_name.upper()}",
            "MCP_TOKEN",
        ]

        for var in env_vars:
            token = os.getenv(var)
            if token:
                return token

        return None

    def clear_token_cache(self) -> None:
        """清除 Token 缓存"""
        self._access_token = None
        self._token_expires_at = 0
