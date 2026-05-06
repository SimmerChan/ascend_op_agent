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

"""RemoteEnvConfig - 远程环境配置

支持镜像/容器配置，用于远程开发环境管理。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class EnvironmentType(Enum):
    """环境类型"""
    HOST = "host"  # 宿主机环境
    AUTO_CONTAINER = "auto_container"  # 自动创建容器
    EXISTING_CONTAINER = "existing_container"  # 已有容器


@dataclass
class EnvironmentInfo:
    """环境信息"""
    type: EnvironmentType
    description: str
    host: str
    user: str
    port: int
    image_name: Optional[str] = None
    container_name: Optional[str] = None


class RemoteEnvConfig:
    """远程开发环境配置

    支持三种模式:
    1. 宿主机环境: 无镜像无容器配置
    2. 自动创建容器: 有镜像无容器
    3. 已有容器: 有镜像有容器
    """

    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        image_name: Optional[str] = None,
        container_name: Optional[str] = None,
    ):
        """
        Args:
            host: 主机地址
            user: 用户名
            port: SSH端口
            key_path: SSH密钥路径
            password: SSH密码（支持 ${ENV_VAR} 格式）
            image_name: Docker镜像名称（可选）
            container_name: 容器名称（可选）
        """
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.image_name = image_name
        self.container_name = container_name

    def get_environment_type(self) -> EnvironmentType:
        """获取环境类型"""
        if self.image_name and self.container_name:
            return EnvironmentType.EXISTING_CONTAINER
        elif self.image_name:
            return EnvironmentType.AUTO_CONTAINER
        else:
            return EnvironmentType.HOST

    def get_environment_description(self) -> str:
        """获取环境描述"""
        env_type = self.get_environment_type()

        if env_type == EnvironmentType.HOST:
            return "宿主机环境开发"
        elif env_type == EnvironmentType.AUTO_CONTAINER:
            return f"自动创建容器开发 (镜像: {self.image_name})"
        else:
            return f"容器内开发 (镜像: {self.image_name}, 容器: {self.container_name})"

    def requires_confirmation(self) -> bool:
        """是否需要用户确认"""
        # 远程环境需要确认
        return True

    def to_environment_info(self) -> EnvironmentInfo:
        """转换为EnvironmentInfo"""
        return EnvironmentInfo(
            type=self.get_environment_type(),
            description=self.get_environment_description(),
            host=self.host,
            user=self.user,
            port=self.port,
            image_name=self.image_name,
            container_name=self.container_name,
        )


class RemoteEnvValidator:
    """远程环境验证器

    支持 Docker 容器生命周期管理。
    """

    # Docker 命令超时（秒）
    _DOCKER_TIMEOUT = 120

    def __init__(self, ssh_manager):
        """
        Args:
            ssh_manager: SSHManager 或 SSHEnvironment 实例
        """
        self.ssh_manager = ssh_manager

    def _exec_docker_command(self, docker_cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """执行 docker 命令

        Args:
            docker_cmd: docker 子命令（如 "ps -a"）
            timeout: 超时时间

        Returns:
            (returncode, stdout, stderr)
        """
        # 构建完整的 SSH 命令
        ssh_cmd = self._build_ssh_command()
        full_cmd = ssh_cmd + [docker_cmd]

        try:
            import subprocess
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    def _build_ssh_command(self) -> list:
        """构建 SSH 命令"""
        ssh_cmd = ["ssh"]

        # 连接参数
        if hasattr(self.ssh_manager, "port"):
            ssh_cmd.extend(["-p", str(self.ssh_manager.port)])

        if hasattr(self.ssh_manager, "key_path") and self.ssh_manager.key_path:
            ssh_cmd.extend(["-i", self.ssh_manager.key_path])

        ssh_cmd.append(f"{self.ssh_manager.user}@{self.ssh_manager.host}")

        return ssh_cmd

    def check_environment(self, config: RemoteEnvConfig) -> EnvironmentInfo:
        """检查远程环境

        Args:
            config: 远程环境配置

        Returns:
            环境信息
        """
        # 确保连接
        if hasattr(self.ssh_manager, "ensure_connected"):
            self.ssh_manager.ensure_connected()

        # 执行实际的连接测试
        try:
            if hasattr(self.ssh_manager, "exec_command"):
                # SSHManager style
                result = self.ssh_manager.exec_command("echo test", timeout=10)
                if not result.success:
                    logger.warning(f"SSH connection test failed: {result.stderr}")
            elif hasattr(self.ssh_manager, "run_bash"):
                # SSHEnvironment style
                handle = self.ssh_manager.run_bash("echo test", timeout=10)
                ret = handle.wait(timeout=15)
                if ret != 0:
                    logger.warning(f"SSH connection test failed: stderr={handle.stderr}")

            # 如果配置了容器，检查容器状态
            if config.container_name:
                container_status = self.check_container_status(config.container_name)
                logger.info(f"Container status: {container_status}")

            return config.to_environment_info()

        except Exception as e:
            logger.error(f"Environment check failed: {e}")
            # 即使检查失败，仍然返回配置信息，但标记状态
            info = config.to_environment_info()
            info.description = f"{info.description} (检查失败: {e})"
            return info

    def check_container_status(self, container_name: str) -> dict:
        """检查容器状态

        Args:
            container_name: 容器名称

        Returns:
            容器状态信息
        """
        # 使用 docker inspect 检查容器状态
        returncode, stdout, stderr = self._exec_docker_command(
            f"docker inspect {container_name} --format '{{{{.State.Running}}}}'",
            timeout=10,
        )

        if returncode == 0:
            running = stdout.strip().lower() == "true"
            return {
                "exists": True,
                "running": running,
                "status": "running" if running else "stopped",
            }

        # 检查是否是 "no such container" 错误
        if "no such object" in stderr.lower() or "no such container" in stderr.lower():
            return {
                "exists": False,
                "running": False,
                "status": "unknown",
            }

        return {
            "exists": False,
            "running": False,
            "status": "unknown",
        }

    def create_container(
        self,
        image_name: str,
        container_name: str,
        volumes: dict | None = None,
        environment: dict | None = None,
    ) -> bool:
        """创建容器

        Args:
            image_name: 镜像名称
            container_name: 容器名称
            volumes:  volumes 映射（如 {"/host/path": "/container/path"}）
            environment: 环境变量（如 {"KEY": "value"}）

        Returns:
            是否成功
        """
        # 构建 docker run 命令
        parts = ["docker run -d --name", container_name]

        # 添加 volumes
        if volumes:
            for host_path, container_path in volumes.items():
                parts.append(f"-v {host_path}:{container_path}")

        # 添加环境变量
        if environment:
            for key, value in environment.items():
                parts.append(f"-e {key}={value}")

        # 添加镜像和入口命令
        parts.append(image_name)
        parts.append("sleep infinity")  # 保持容器运行

        docker_cmd = " ".join(parts)

        returncode, _, stderr = self._exec_docker_command(docker_cmd, timeout=self._DOCKER_TIMEOUT)

        if returncode == 0:
            return True

        # 检查是否是容器已存在的错误
        if "Conflict" in stderr or "already exists" in stderr.lower():
            # 容器已存在，尝试启动
            return self.start_container(container_name)

        return False

    def start_container(self, container_name: str) -> bool:
        """启动容器

        Args:
            container_name: 容器名称

        Returns:
            是否成功
        """
        returncode, _, stderr = self._exec_docker_command(
            f"docker start {container_name}",
            timeout=30,
        )

        if returncode == 0:
            return True

        # 容器可能已处于运行状态
        if "is already running" in stderr:
            return True

        return False

    def stop_container(self, container_name: str, timeout: int = 30) -> bool:
        """停止容器

        Args:
            container_name: 容器名称
            timeout: 停止超时时间（秒）

        Returns:
            是否成功
        """
        returncode, _, stderr = self._exec_docker_command(
            f"docker stop -t {timeout} {container_name}",
            timeout=timeout + 10,
        )

        if returncode == 0:
            return True

        # 容器可能已处于停止状态
        if "is not running" in stderr or "already stopped" in stderr:
            return True

        return False

    def remove_container(self, container_name: str, force: bool = False) -> bool:
        """删除容器

        Args:
            container_name: 容器名称
            force: 是否强制删除（运行中的容器）

        Returns:
            是否成功
        """
        cmd = f"docker rm {'-f' if force else ''} {container_name}".strip()
        returncode, _, stderr = self._exec_docker_command(cmd, timeout=30)

        if returncode == 0:
            return True

        # 容器可能不存在
        if "no such object" in stderr.lower() or "no such container" in stderr.lower():
            return True

        return False

    def list_containers(self, all: bool = True) -> list[dict]:
        """列出容器

        Args:
            all: 是否显示所有容器（包括已停止）

        Returns:
            容器列表
        """
        cmd = "docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.State}}'"
        returncode, stdout, stderr = self._exec_docker_command(cmd, timeout=10)

        if returncode != 0:
            return []

        containers = []
        for line in stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 5:
                containers.append({
                    "id": parts[0],
                    "name": parts[1],
                    "image": parts[2],
                    "status": parts[3],
                    "state": parts[4],
                })

        return containers
