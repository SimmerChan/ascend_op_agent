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
    """远程环境验证器"""

    def __init__(self, ssh_manager):
        """
        Args:
            ssh_manager: SSHManager实例
        """
        self.ssh_manager = ssh_manager

    def check_environment(self, config: RemoteEnvConfig) -> EnvironmentInfo:
        """检查远程环境

        Args:
            config: 远程环境配置

        Returns:
            环境信息
        """
        # 确保连接
        self.ssh_manager.ensure_connected()

        # TODO: 实际检查环境是否可用
        # 目前返回配置中的信息
        return config.to_environment_info()

    def check_container_status(self, container_name: str) -> dict:
        """检查容器状态

        Args:
            container_name: 容器名称

        Returns:
            容器状态信息
        """
        # TODO: 实现容器状态检查
        # 使用 docker inspect 或 nerdctl
        return {
            "exists": False,
            "running": False,
            "status": "unknown",
        }

    def create_container(self, image_name: str, container_name: str) -> bool:
        """创建容器

        Args:
            image_name: 镜像名称
            container_name: 容器名称

        Returns:
            是否成功
        """
        # TODO: 实现容器创建
        # 使用 docker run 或 nerdctl
        return False

    def start_container(self, container_name: str) -> bool:
        """启动容器

        Args:
            container_name: 容器名称

        Returns:
            是否成功
        """
        # TODO: 实现容器启动
        return False

    def stop_container(self, container_name: str) -> bool:
        """停止容器

        Args:
            container_name: 容器名称

        Returns:
            是否成功
        """
        # TODO: 实现容器停止
        return False
