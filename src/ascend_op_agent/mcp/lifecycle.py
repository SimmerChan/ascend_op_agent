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

"""MCPLifecycleManager - MCP服务器生命周期管理"""

import subprocess
import threading
import time
from typing import Optional

from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType


class MCPProcessError(Exception):
    """MCP进程错误"""
    pass


class MCPLifecycleManager:
    """MCP服务器生命周期管理器

    负责:
    - 启动/停止 MCP 服务器进程
    - 健康检查
    - 清理所有服务器
    """

    def __init__(self):
        self.processes: dict[str, subprocess.Popen] = {}
        self.health_checks: dict[str, float] = {}
        self._health_check_thread: Optional[threading.Thread] = None
        self._running = False

    def start_server(self, config: MCPServerConfig) -> bool:
        """启动 MCP 服务器

        Args:
            config: MCP 服务器配置

        Returns:
            是否启动成功
        """
        if config.name in self.processes:
            # 已存在，先停止
            self.stop_server(config.name)

        if config.type == TransportType.STDIO:
            return self._start_stdio_server(config)
        elif config.type in (TransportType.HTTP, TransportType.STREAMABLE_HTTP):
            # HTTP 模式不需要启动进程，只是配置连接信息
            self.processes[config.name] = None
            return True
        else:
            raise ValueError(f"Unsupported transport type: {config.type}")

    def _start_stdio_server(self, config: MCPServerConfig) -> bool:
        """启动 stdio 模式的 MCP 服务器"""
        if not config.command:
            raise ValueError(f"stdio mode requires command for server {config.name}")

        try:
            # 构建命令
            cmd = config.command.split()
            if config.args:
                cmd.extend(config.args)

            # 启动进程
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.processes[config.name] = proc
            self.health_checks[config.name] = time.time()

            return True

        except Exception as e:
            raise MCPProcessError(f"Failed to start MCP server {config.name}: {e}")

    def stop_server(self, name: str) -> bool:
        """停止 MCP 服务器

        Args:
            name: 服务器名称

        Returns:
            是否停止成功
        """
        if name not in self.processes:
            return True

        proc = self.processes[name]

        if proc is None:
            # HTTP 模式没有进程
            del self.processes[name]
            if name in self.health_checks:
                del self.health_checks[name]
            return True

        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            del self.processes[name]
            if name in self.health_checks:
                del self.health_checks[name]

            return True

        except Exception as e:
            raise MCPProcessError(f"Failed to stop MCP server {name}: {e}")

    def health_check(self, name: str) -> bool:
        """检查服务器健康状态

        Args:
            name: 服务器名称

        Returns:
            是否健康
        """
        if name not in self.processes:
            return False

        proc = self.processes[name]

        if proc is None:
            # HTTP 模式，假设健康
            return True

        # 检查进程是否还在运行
        if proc.poll() is not None:
            # 进程已退出
            return False

        # 检查是否有输出（可能表明进程崩溃）
        try:
            import select
            if select.select([proc.stderr], [], [], 0)[0]:
                # 有错误输出，可能有问题
                return False
        except Exception:
            pass

        return True

    def start_health_check_thread(self, interval: int = 30) -> None:
        """启动健康检查线程

        Args:
            interval: 检查间隔（秒）
        """
        if self._running:
            return

        self._running = True
        self._health_check_thread = threading.Thread(
            target=self._health_check_loop,
            args=(interval,),
            daemon=True,
        )
        self._health_check_thread.start()

    def stop_health_check_thread(self) -> None:
        """停止健康检查线程"""
        self._running = False
        if self._health_check_thread:
            self._health_check_thread.join(timeout=5)

    def _health_check_loop(self, interval: int) -> None:
        """健康检查循环"""
        while self._running:
            for name in list(self.processes.keys()):
                if not self.health_check(name):
                    # 记录不健康的服务器
                    self.health_checks[name] = 0  # 0 表示不健康

            time.sleep(interval)

    def cleanup(self) -> None:
        """清理所有服务器"""
        self.stop_health_check_thread()

        for name in list(self.processes.keys()):
            try:
                self.stop_server(name)
            except Exception:
                pass

        self.processes.clear()
        self.health_checks.clear()

    def is_server_running(self, name: str) -> bool:
        """检查服务器是否在运行"""
        return name in self.processes and self.health_check(name)

    def get_server_status(self, name: str) -> dict:
        """获取服务器状态"""
        is_running = self.is_server_running(name)
        last_check = self.health_checks.get(name, 0)

        return {
            "name": name,
            "running": is_running,
            "last_check": last_check,
        }

    def __enter__(self) -> "MCPLifecycleManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()
