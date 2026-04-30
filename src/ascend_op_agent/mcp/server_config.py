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

"""MCPServerConfig - MCP服务器配置"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TransportType(Enum):
    """传输类型"""
    STDIO = "stdio"
    HTTP = "http"
    STREAMABLE_HTTP = "streamable-http"


@dataclass
class MCPServerConfig:
    """MCP服务器配置

    支持三种传输模式:
    - stdio: 标准输入输出（本地进程）
    - http: HTTP 请求响应
    - streamable-http: 流式 HTTP（支持 long polling）
    """
    name: str
    type: TransportType = TransportType.STDIO
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None
    token: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    oauth: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        """从字典创建配置"""
        # 转换 type 字符串到枚举
        if isinstance(data.get("type"), str):
            data = dict(data)
            type_str = data["type"].lower()
            if type_str == "stdio":
                data["type"] = TransportType.STDIO
            elif type_str == "http":
                data["type"] = TransportType.HTTP
            elif type_str in ("streamable-http", "streamable"):
                data["type"] = TransportType.STREAMABLE_HTTP
            else:
                data["type"] = TransportType.STDIO

        return cls(**data)

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "name": self.name,
            "type": self.type.value,
        }
        if self.command:
            result["command"] = self.command
        if self.args:
            result["args"] = self.args
        if self.url:
            result["url"] = self.url
        if self.token:
            result["token"] = self.token
        if self.headers:
            result["headers"] = self.headers
        if self.oauth:
            result["oauth"] = self.oauth
        return result

    def requires_oauth(self) -> bool:
        """是否需要 OAuth 认证"""
        return self.oauth is not None and self.oauth.get("type") == "oauth"

    def get_auth_header(self) -> Optional[dict]:
        """获取认证头"""
        if self.token:
            # 支持 ${ENV_VAR} 格式的环境变量引用
            import os
            import re

            token = self.token
            pattern = re.compile(r'\$\{([^}]+)\}')
            matches = pattern.findall(token)
            for env_var in matches:
                token = token.replace(f"${{{env_var}}}", os.getenv(env_var, ""))

            return {"Authorization": f"Bearer {token}"}
        return None
