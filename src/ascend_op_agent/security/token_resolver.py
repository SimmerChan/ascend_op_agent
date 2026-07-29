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

"""TokenResolver - Token解析器

支持 ${ENV_VAR} 格式的环境变量引用，用于MCP bearer token等敏感信息。
"""

import os
import re
from typing import Optional


class TokenResolver:
    """Token解析器

    支持 ${ENV_VAR} 格式的环境变量引用。
    """

    # 环境变量引用模式: ${VAR_NAME}
    ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

    def __init__(self):
        pass

    def resolve_token(self, token: Optional[str]) -> Optional[str]:
        """解析Token

        Args:
            token: Token值（支持 ${ENV_VAR} 格式）

        Returns:
            解析后的Token
        """
        if not token:
            return None

        def replace_env_var(match):
            env_var = match.group(1)
            return os.getenv(env_var, "")

        resolved = self.ENV_VAR_PATTERN.sub(replace_env_var, token)
        return resolved if resolved else None

    def get_bearer_token(self, token: Optional[str]) -> Optional[str]:
        """获取Bearer Token

        Args:
            token: Token值（支持 ${ENV_VAR} 格式）

        Returns:
            解析后的Bearer Token（格式: Bearer xxx）
        """
        resolved = self.resolve_token(token)
        if not resolved:
            return None
        return f"Bearer {resolved}"

    def get_auth_header(self, token: Optional[str]) -> Optional[dict]:
        """获取认证头

        Args:
            token: Token值（支持 ${ENV_VAR} 格式）

        Returns:
            认证头字典
        """
        bearer = self.get_bearer_token(token)
        if not bearer:
            return None
        return {"Authorization": bearer}

    def has_env_var_reference(self, token: str) -> bool:
        """检查Token是否包含环境变量引用

        Args:
            token: 要检查的Token

        Returns:
            是否包含环境变量引用
        """
        if not token:
            return False
        return bool(self.ENV_VAR_PATTERN.search(token))

    def extract_env_vars(self, token: str) -> list[str]:
        """提取Token中的所有环境变量名

        Args:
            token: 要检查的Token

        Returns:
            环境变量名列表
        """
        if not token:
            return []
        return self.ENV_VAR_PATTERN.findall(token)
