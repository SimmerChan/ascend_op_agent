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

"""CredentialManager - 凭据管理器

支持配置文件和环境变量引用的凭据解析。
"""

import os
import re
from typing import Optional


class CredentialManager:
    """凭据管理器

    支持 ${ENV_VAR} 格式的环境变量引用。
    """

    # 环境变量引用模式: ${VAR_NAME}
    ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

    def __init__(self):
        pass

    def resolve_env_var(self, value: str) -> str:
        """解析环境变量引用

        Args:
            value: 可能包含 ${ENV_VAR} 的值

        Returns:
            解析后的值
        """
        if not value:
            return value

        def replace_env_var(match):
            env_var = match.group(1)
            return os.getenv(env_var, "")

        return self.ENV_VAR_PATTERN.sub(replace_env_var, value)

    def get_ssh_password(
        self,
        password: Optional[str],
        host: Optional[str] = None,
    ) -> Optional[str]:
        """获取SSH密码

        Args:
            password: 密码（支持 ${ENV_VAR} 格式）
            host: 主机地址（用于查找特定主机的密码）

        Returns:
            解析后的密码
        """
        if not password:
            return None

        # 解析环境变量引用
        resolved = self.resolve_env_var(password)
        return resolved if resolved else None

    def get_ssh_key_path(self, key_path: Optional[str]) -> Optional[str]:
        """获取SSH密钥路径

        Args:
            key_path: 密钥路径（支持 ${ENV_VAR} 格式）

        Returns:
            解析后的路径
        """
        if not key_path:
            return None

        resolved = self.resolve_env_var(key_path)
        return resolved if resolved else None

    def has_env_var_reference(self, value: str) -> bool:
        """检查值是否包含环境变量引用

        Args:
            value: 要检查的值

        Returns:
            是否包含环境变量引用
        """
        if not value:
            return False
        return bool(self.ENV_VAR_PATTERN.search(value))

    def extract_env_vars(self, value: str) -> list[str]:
        """提取值中的所有环境变量名

        Args:
            value: 要检查的值

        Returns:
            环境变量名列表
        """
        if not value:
            return []
        return self.ENV_VAR_PATTERN.findall(value)
