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

"""SSHManager - SSH连接管理

使用paramiko实现SSH连接，支持指数退避重连策略。
"""

import time
from dataclasses import dataclass
from typing import Optional

import paramiko


class SSHConnectionError(Exception):
    """SSH连接错误"""
    pass


@dataclass
class CommandResult:
    """命令执行结果"""
    stdout: str
    stderr: str
    return_code: int
    success: bool

    @classmethod
    def from_paramiko(cls, stdout: bytes, stderr: bytes, return_code: int) -> "CommandResult":
        return cls(
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=stderr.decode('utf-8', errors='replace'),
            return_code=return_code,
            success=return_code == 0,
        )


class SSHManager:
    """SSH管理器

    使用paramiko实现SSH连接和命令执行，支持指数退避重连。
    """

    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        """
        Args:
            host: 主机地址
            user: 用户名
            port: SSH端口，默认22
            key_path: SSH密钥路径
            password: SSH密码（支持环境变量引用）
            max_retries: 最大重试次数
            backoff_factor: 退避因子
        """
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self._client: Optional[paramiko.SSHClient] = None
        self._connected = False

    def connect(self) -> bool:
        """建立SSH连接

        使用指数退避重连策略。

        Returns:
            连接是否成功

        Raises:
            SSHConnectionError: 连接失败
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # 连接参数
                connect_kwargs: dict = {
                    'hostname': self.host,
                    'port': self.port,
                    'username': self.user,
                    'timeout': 30,
                }

                # 认证方式
                if self.key_path:
                    connect_kwargs['key_filename'] = self.key_path
                elif self.password:
                    connect_kwargs['password'] = self.password

                client.connect(**connect_kwargs)
                self._client = client
                self._connected = True
                return True

            except paramiko.AuthenticationException as e:
                raise SSHConnectionError(f"认证失败: {e}")
            except paramiko.SSHException as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = self.backoff_factor ** attempt
                    time.sleep(wait_time)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = self.backoff_factor ** attempt
                    time.sleep(wait_time)

        raise SSHConnectionError(f"连接失败 (尝试{self.max_retries}次): {last_error}")

    def disconnect(self) -> None:
        """断开SSH连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._connected = False

    def exec_command(self, cmd: str, timeout: int = 300) -> CommandResult:
        """执行远程命令

        Args:
            cmd: 要执行的命令
            timeout: 超时时间（秒）

        Returns:
            命令执行结果

        Raises:
            SSHConnectionError: 未连接或执行失败
        """
        if not self._connected or not self._client:
            if not self.connect():
                raise SSHConnectionError("未连接到SSH服务器")

        try:
            stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
            return_code = stdout.channel.recv_exit_status()

            return CommandResult.from_paramiko(
                stdout.read(),
                stderr.read(),
                return_code,
            )
        except Exception as e:
            raise SSHConnectionError(f"命令执行失败: {e}")

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._connected or not self._client:
            return False

        try:
            # 发送空命令检查连接
            transport = self._client.get_transport()
            if transport and transport.is_active():
                return True
        except Exception:
            pass

        self._connected = False
        return False

    def ensure_connected(self) -> None:
        """确保已连接，如未连接则重新连接"""
        if not self.is_connected():
            self.connect()

    def __enter__(self) -> "SSHManager":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()


def create_ssh_manager_from_config(
    host: str,
    user: str,
    port: int = 22,
    key_path: Optional[str] = None,
    password: Optional[str] = None,
) -> SSHManager:
    """从配置创建SSHManager

    Args:
        host: 主机地址
        user: 用户名
        port: SSH端口
        key_path: SSH密钥路径
        password: SSH密码（支持 ${ENV_VAR} 格式的环境变量引用）

    Returns:
        SSHManager实例
    """
    # 解析环境变量引用
    if password and password.startswith('${') and password.endswith('}'):
        import os
        env_var = password[2:-1]
        password = os.getenv(env_var, "")

    if key_path and key_path.startswith('${') and key_path.endswith('}'):
        import os
        env_var = key_path[2:-1]
        key_path = os.getenv(env_var, "")

    return SSHManager(
        host=host,
        user=user,
        port=port,
        key_path=key_path if key_path else None,
        password=password if password else None,
    )
