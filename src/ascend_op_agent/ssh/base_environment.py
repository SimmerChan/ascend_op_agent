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

"""BaseEnvironment - 抽象后端接口

定义统一后端接口，支持 Local/SSH/Docker/Modal/Daytona/Singularity 等多种后端。
"""

from __future__ import annotations

import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


class ProcessHandle(Protocol):
    """进程句柄协议

    抽象所有后端（subprocess/SDK）的进程执行接口。
    """

    def poll(self) -> Optional[int]:
        """轮询进程状态

        Returns:
            如果进程已结束返回返回码，否则返回 None
        """
        ...

    def kill(self) -> None:
        """终止进程"""
        ...

    def wait(self, timeout: Optional[float] = None) -> int:
        """等待进程结束

        Args:
            timeout: 超时时间（秒）

        Returns:
            进程返回码

        Raises:
            TimeoutError: 等待超时
        """
        ...

    @property
    def stdout(self) -> str:
        """标准输出"""
        ...

    @property
    def stderr(self) -> str:
        """标准错误"""
        ...

    @property
    def returncode(self) -> Optional[int]:
        """返回码"""
        ...


@dataclass
class ExecuteResult:
    """命令执行结果"""
    stdout: str
    stderr: str
    return_code: int
    success: bool
    timed_out: bool = False

    @classmethod
    def from_process_handle(cls, handle: ProcessHandle, timed_out: bool = False) -> "ExecuteResult":
        return cls(
            stdout=handle.stdout,
            stderr=handle.stderr,
            return_code=handle.returncode if handle.returncode is not None else -1,
            success=handle.returncode == 0,
            timed_out=timed_out,
        )


class _ThreadedProcessHandle:
    """线程化进程句柄

    封装异步执行函数（非subprocess后端如Modal/Daytona使用），
    统一 ProcessHandle 接口。
    """

    def __init__(
        self,
        target_fn: Callable[..., tuple[str, str, int]],
        args: tuple = (),
        kwargs: dict | None = None,
    ):
        """
        Args:
            target_fn: 异步执行函数，签名为 () -> (stdout, stderr, returncode)
            args: 位置参数
            kwargs: 关键字参数
        """
        self._target_fn = target_fn
        self._args = args
        self._kwargs = kwargs or {}
        self._result: Optional[tuple[str, str, int]] = None
        self._exc: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None
        self._returncode: Optional[int] = None
        self._stdout: str = ""
        self._stderr: str = ""

    def _run(self) -> None:
        """在线程中执行目标函数"""
        try:
            self._stdout, self._stderr, self._returncode = self._target_fn(*self._args, **self._kwargs)
        except Exception as e:
            self._exc = e
            self._returncode = -1
            self._stderr = str(e)

    def start(self) -> None:
        """启动线程执行"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def poll(self) -> Optional[int]:
        """轮询进程状态"""
        if self._thread is None:
            return None
        if not self._thread.is_alive():
            return self._returncode
        return None

    def kill(self) -> None:
        """终止进程（对于线程化执行，当前不支持）"""
        # 注意：线程不支持强制终止，这是线程化后端的限制
        # 调用方需要通过其他机制（如取消标志）来停止执行
        pass

    def wait(self, timeout: Optional[float] = None) -> int:
        """等待线程结束"""
        if self._thread is None:
            return self._returncode or -1

        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise TimeoutError(f"进程执行超时 (timeout={timeout}s)")
        return self._returncode if self._returncode is not None else -1

    @property
    def stdout(self) -> str:
        """标准输出"""
        # 等待线程完成以获取结果
        if self._thread is not None:
            self._thread.join(timeout=30)
        return self._stdout

    @property
    def stderr(self) -> str:
        """标准错误"""
        if self._thread is not None:
            self._thread.join(timeout=30)
        return self._stderr

    @property
    def returncode(self) -> Optional[int]:
        """返回码"""
        if self._thread is not None and not self._thread.is_alive():
            return self._returncode
        return None


@dataclass
class BaseEnvironment(ABC):
    """远程执行环境抽象基类

    定义统一后端接口，支持透明切换不同后端实现。
    子类必须实现 _run_bash() 方法。
    """

    cwd: str = field(default="/tmp")
    timeout: int = field(default=300)

    @abstractmethod
    def _run_bash(self, command: str, timeout: int) -> ProcessHandle:
        """执行bash命令（抽象方法）

        Args:
            command: 要执行的命令
            timeout: 超时时间（秒）

        Returns:
            ProcessHandle 实例
        """
        ...

    def execute(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None,
        stdin_data: Optional[str] = None,
    ) -> ExecuteResult:
        """执行命令

        Args:
            command: 要执行的命令
            cwd: 工作目录（默认为 self.cwd）
            timeout: 超时时间（秒，默认 self.timeout）
            stdin_data: 标准输入数据（可选）

        Returns:
            ExecuteResult 执行结果
        """
        target_cwd = cwd or self.cwd
        target_timeout = timeout if timeout is not None else self.timeout

        # 前置处理（子类可覆盖）
        self._before_execute()

        # 包装命令（注入snapshot source、CWD marker等）
        wrapped_cmd = self._wrap_command(command, target_cwd, stdin_data)

        # 执行命令
        handle = self._run_bash(wrapped_cmd, target_timeout)

        # 等待完成
        timed_out = False
        try:
            handle.wait(timeout=target_timeout)
        except TimeoutError:
            timed_out = True
            handle.kill()
            handle.wait()

        # 后置处理（子类可覆盖）
        self._after_execute(handle)

        return ExecuteResult.from_process_handle(handle, timed_out=timed_out)

    def _before_execute(self) -> None:
        """执行前预处理（可被子类覆盖）"""
        pass

    def _after_execute(self, handle: ProcessHandle) -> None:
        """执行后后处理（可被子类覆盖）"""
        pass

    def _wrap_command(
        self,
        command: str,
        cwd: str,
        stdin_data: Optional[str] = None,
    ) -> str:
        """包装命令（可被子类覆盖）

        默认实现仅切换到目标目录并执行命令。

        Args:
            command: 原始命令
            cwd: 工作目录
            stdin_data: 标准输入数据

        Returns:
            包装后的命令
        """
        import shlex
        return f"cd {shlex.quote(cwd)} && {command}"

    def init_session(self) -> None:
        """初始化会话环境

        捕获当前shell状态到快照文件，供后续命令复用。
        可被子类覆盖。
        """
        pass

    def cleanup(self) -> None:
        """清理会话环境

        断开连接、清理临时文件等。
        可被子类覆盖。
        """
        pass

    def __enter__(self) -> "BaseEnvironment":
        self.init_session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()