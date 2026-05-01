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

.. deprecated::
    SSHManager 将在未来版本中替换为 SSHEnvironment。
    请使用 SSHEnvironment 获取更好的性能和更多功能（ControlMaster连接复用、会话快照等）。
"""

import hashlib
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
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
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            return_code=return_code,
            success=return_code == 0,
        )


class SSHManager:
    """SSH管理器

    使用paramiko实现SSH连接和命令执行，支持指数退避重连。

    .. deprecated::
        请使用 SSHEnvironment 获取更好的性能和更多功能。
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
                    "hostname": self.host,
                    "port": self.port,
                    "username": self.user,
                    "timeout": 30,
                }

                # 认证方式
                if self.key_path:
                    connect_kwargs["key_filename"] = self.key_path
                elif self.password:
                    connect_kwargs["password"] = self.password

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
    if password and password.startswith("${") and password.endswith("}"):
        import os
        env_var = password[2:-1]
        password = os.getenv(env_var, "")

    if key_path and key_path.startswith("${") and key_path.endswith("}"):
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


# =============================================================================
# SSHEnvironment - 基于 BaseEnvironment 的新版SSH实现
# =============================================================================


from ascend_op_agent.ssh.base_environment import (
    BaseEnvironment,
    ProcessHandle,
    ExecuteResult,
    _ThreadedProcessHandle,
)
from enum import Enum


class _SSHProcessHandle:
    """SSH进程句柄

    封装paramiko的exec_command返回的stdin/stdout/stderr。
    """

    def __init__(
        self,
        client: paramiko.SSHClient,
        command: str,
        timeout: int = 300,
    ):
        self._client = client
        self._command = command
        self._timeout = timeout
        self._channel = None
        self._returncode: Optional[int] = None
        self._stdout: str = ""
        self._stderr: str = ""

    def _get_channel(self):
        """获取或创建channel（懒加载）"""
        if self._channel is None:
            self._channel = self._client.get_transport().open_session()
            self._channel.settimeout(self._timeout)
            self._channel.exec_command(self._command)
        return self._channel

    def poll(self) -> Optional[int]:
        """轮询进程状态"""
        channel = self._get_channel()
        if channel.exit_status_ready():
            self._returncode = channel.recv_exit_status()
            return self._returncode
        return None

    def kill(self) -> None:
        """终止进程"""
        channel = self._get_channel()
        channel.close()

    def wait(self, timeout: Optional[float] = None) -> int:
        """等待进程结束"""
        channel = self._get_channel()

        if timeout:
            channel.settimeout(timeout)

        # 等待命令完成
        while not channel.exit_status_ready():
            time.sleep(0.1)

        self._returncode = channel.recv_exit_status()
        return self._returncode

    def _drain_output(self, channel) -> None:
        """排空输出缓冲区"""
        # 读取所有可用的 stdout
        stdout_buffer = b""
        stderr_buffer = b""

        while channel.recv_ready():
            stdout_buffer += channel.recv(4096)
        while channel.recv_stderr_ready():
            stderr_buffer += channel.recv_stderr(4096)

        self._stdout = stdout_buffer.decode("utf-8", errors="replace")
        self._stderr = stderr_buffer.decode("utf-8", errors="replace")

    @property
    def stdout(self) -> str:
        """标准输出"""
        if self._channel and self._channel.recv_ready():
            self._drain_output(self._channel)
        return self._stdout

    @property
    def stderr(self) -> str:
        """标准错误"""
        if self._channel and self._channel.recv_stderr_ready():
            self._drain_output(self._channel)
        return self._stderr

    @property
    def returncode(self) -> Optional[int]:
        """返回码"""
        if self._returncode is None and self._channel:
            if self._channel.exit_status_ready():
                self._returncode = self._channel.recv_exit_status()
        return self._returncode


class SSHEnvironment(BaseEnvironment):
    """SSH执行环境

    基于 BaseEnvironment 的SSH实现，支持：
    - ControlMaster连接复用
    - 会话快照机制
    - CWD持久化
    """

    # 会话快照标记
    CWD_MARKER_PREFIX = "__HERMES_CWD_"
    CWD_MARKER_SUFFIX = "__HERMES_CWD__"

    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        cwd: str = "/tmp",
        timeout: int = 300,
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
            cwd: 默认工作目录
            timeout: 默认超时时间
            max_retries: 最大重试次数
            backoff_factor: 退避因子
        """
        super().__init__(cwd=cwd, timeout=timeout)

        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        # ControlMaster socket 路径
        self._temp_dir = Path(tempfile.gettempdir())
        self._control_dir = self._temp_dir / "hermes-ssh"
        self._control_dir.mkdir(parents=True, exist_ok=True)

        # 使用 host:port:user 的hash作为control socket文件名
        # 避免macOS上104字节的路径名长度限制
        identity = f"{host}:{port}:{user}"
        socket_hash = hashlib.sha1(identity.encode()).hexdigest()[:16]
        self._control_socket = self._control_dir / f"{socket_hash}.sock"

        self._client: Optional[paramiko.SSHClient] = None
        self._connected = False

        # 会话快照
        self._session_id = socket_hash
        self._snapshot_path = self._temp_dir / f"hermes-snap-{self._session_id}.sh"
        self._snapshot_ready = False

        # 实际工作目录
        self._cwd = cwd

        # 命令执行计数（用于调试）
        self._exec_count = 0

    def _get_connection_hash(self) -> str:
        """获取连接标识hash"""
        identity = f"{self.host}:{self.port}:{self.user}"
        return hashlib.sha1(identity.encode()).hexdigest()[:16]

    def connect(self) -> bool:
        """建立SSH连接（使用ControlMaster）"""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                connect_kwargs: dict = {
                    "hostname": self.host,
                    "port": self.port,
                    "username": self.user,
                    "timeout": 30,
                }

                if self.key_path:
                    connect_kwargs["key_filename"] = self.key_path
                elif self.password:
                    connect_kwargs["password"] = self.password

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

    def _run_bash(self, command: str, timeout: int) -> ProcessHandle:
        """执行bash命令"""
        if not self._connected or not self._client:
            self.connect()

        self._exec_count += 1
        handle = _SSHProcessHandle(self._client, command, timeout)
        return handle

    def init_session(self) -> None:
        """初始化会话快照

        捕获远程shell环境到快照文件。
        """
        # 构建bootstrap脚本
        bootstrap_script = [
            f"export -p > {self._snapshot_path}",
            f"declare -f >> {self._snapshot_path}",
            f"alias -p >> {self._snapshot_path}",
            f"echo 'shopt -s expand_aliases' >> {self._snapshot_path}",
        ]

        init_cmd = " && ".join(bootstrap_script)

        try:
            result = self._run_bash(init_cmd, 30).wait()
            if result == 0:
                self._snapshot_ready = True
        except Exception:
            # 如果快照创建失败，尝试使用 bash -l 作为回退
            self._snapshot_ready = False

    def _wrap_command(
        self,
        command: str,
        cwd: str,
        stdin_data: Optional[str] = None,
    ) -> str:
        """包装命令

        注入：
        1. source 快照文件（恢复环境变量）
        2. cd 到目标目录
        3. 执行命令
        4. 保存快照（持久化环境变量变更）
        5. 打印CWD标记（追踪目录变化）
        """
        parts = []

        # 1. source 快照文件
        if self._snapshot_ready and self._snapshot_path.exists():
            parts.append(f"source {self._snapshot_path} >/dev/null 2>&1 || true")

        # 2. cd 到目标目录
        parts.append(f"builtin cd {shlex.quote(cwd)} || exit 126")

        # 3. 执行命令
        escaped_cmd = command.replace("'", "'\\''")
        parts.append(f"eval '{escaped_cmd}'")

        # 4. 保存快照
        if self._snapshot_ready:
            parts.append(f"export -p > {self._snapshot_path} 2>/dev/null || true")

        # 5. 打印CWD标记
        marker = f"{self.CWD_MARKER_PREFIX}{self._session_id}"
        parts.append(f"printf '\\n{marker}%s{self.CWD_MARKER_SUFFIX}\\n' \"$(pwd -P)\"")

        return "\n".join(parts)

    def _before_execute(self) -> None:
        """执行前预处理"""
        # 确保连接
        if not self._connected:
            self.connect()

    def _after_execute(self, handle: ProcessHandle) -> None:
        """执行后后处理

        解析stdout中的CWD标记，更新当前目录。
        """
        # 从stdout中解析CWD标记
        marker_prefix = f"{self.CWD_MARKER_PREFIX}{self._session_id}"
        marker_suffix = self.CWD_MARKER_SUFFIX

        stdout = handle.stdout
        lines = stdout.split("\n")
        for line in lines:
            if marker_prefix in line and marker_suffix in line:
                # 提取CWD
                start = line.index(marker_prefix) + len(marker_prefix)
                end = line.index(marker_suffix)
                new_cwd = line[start:end].strip()
                if new_cwd:
                    self._cwd = new_cwd
                break

    def cleanup(self) -> None:
        """清理会话环境

        断开SSH连接。
        """
        if self._client:
            self._client.close()
            self._client = None
            self._connected = False

    def is_connected(self) -> bool:
        """检查连接状态"""
        if not self._connected or not self._client:
            return False

        try:
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

    @property
    def cwd(self) -> str:
        """当前工作目录"""
        return self._cwd

    @cwd.setter
    def cwd(self, value: str) -> None:
        """设置工作目录"""
        self._cwd = value

    def __enter__(self) -> "SSHEnvironment":
        self.connect()
        self.init_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()