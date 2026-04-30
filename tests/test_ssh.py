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

"""SSH模块测试"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascend_op_agent.ssh.manager import (
    SSHManager,
    SSHConnectionError,
    CommandResult,
    create_ssh_manager_from_config,
)
from ascend_op_agent.ssh.sync import FileSync, SyncDirection, SyncResult
from ascend_op_agent.ssh.env_config import (
    RemoteEnvConfig,
    RemoteEnvValidator,
    EnvironmentType,
    EnvironmentInfo,
)


class TestSSHManager:
    """SSHManager测试"""

    def test_ssh_manager_creation(self):
        """测试SSHManager创建"""
        manager = SSHManager(
            host="192.168.1.100",
            user="root",
            port=22,
        )
        assert manager.host == "192.168.1.100"
        assert manager.user == "root"
        assert manager.port == 22
        assert manager.max_retries == 3

    def test_ssh_manager_with_password(self):
        """测试带密码的SSHManager"""
        manager = SSHManager(
            host="192.168.1.100",
            user="root",
            password="secret",
        )
        assert manager.password == "secret"

    def test_ssh_manager_default_values(self):
        """测试默认值"""
        manager = SSHManager(host="192.168.1.100", user="root")
        assert manager.port == 22
        assert manager.max_retries == 3
        assert manager.backoff_factor == 2.0

    def test_is_connected_false_when_not_connected(self):
        """测试未连接时返回False"""
        manager = SSHManager(host="192.168.1.100", user="root")
        assert manager.is_connected() is False

    def test_disconnect_when_not_connected(self):
        """测试断开未连接的连接"""
        manager = SSHManager(host="192.168.1.100", user="root")
        # 不应抛出异常
        manager.disconnect()
        assert manager.is_connected() is False


class TestCommandResult:
    """CommandResult测试"""

    def test_from_paramiko_success(self):
        """测试成功命令结果"""
        result = CommandResult.from_paramiko(
            b"output",
            b"",
            0,
        )
        assert result.stdout == "output"
        assert result.stderr == ""
        assert result.return_code == 0
        assert result.success is True

    def test_from_paramiko_failure(self):
        """测试失败命令结果"""
        result = CommandResult.from_paramiko(
            b"",
            b"error",
            1,
        )
        assert result.stdout == ""
        assert result.stderr == "error"
        assert result.return_code == 1
        assert result.success is False


class TestCreateSSHManagerFromConfig:
    """create_ssh_manager_from_config测试"""

    def test_create_with_direct_password(self):
        """测试直接密码"""
        manager = create_ssh_manager_from_config(
            host="192.168.1.100",
            user="root",
            password="secret",
        )
        assert manager.password == "secret"

    def test_create_with_env_var_reference(self):
        """测试环境变量引用"""
        os.environ["TEST_SSH_PASSWORD"] = "env_secret"

        manager = create_ssh_manager_from_config(
            host="192.168.1.100",
            user="root",
            password="${TEST_SSH_PASSWORD}",
        )
        assert manager.password == "env_secret"

        del os.environ["TEST_SSH_PASSWORD"]

    def test_create_with_empty_env_var(self):
        """测试空环境变量"""
        manager = create_ssh_manager_from_config(
            host="192.168.1.100",
            user="root",
            password="${NON_EXISTENT_VAR}",
        )
        # 不存在的环境变量解析为空字符串，然后转为None
        assert manager.password is None or manager.password == ""


class TestFileSync:
    """FileSync测试"""

    def test_sync_direction_enum(self):
        """测试SyncDirection枚举"""
        assert SyncDirection.PUSH.value == "push"
        assert SyncDirection.PULL.value == "pull"

    def test_sync_result_creation(self):
        """测试SyncResult创建"""
        result = SyncResult(
            success=True,
            direction=SyncDirection.PUSH,
            message="Success",
            files_synced=5,
        )
        assert result.success is True
        assert result.direction == SyncDirection.PUSH
        assert result.files_synced == 5

    def test_compute_file_hash(self):
        """测试文件哈希计算"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            hash1 = FileSync.compute_file_hash(temp_path)
            assert len(hash1) == 32  # MD5 hash length

            # 相同内容应该产生相同哈希
            hash2 = FileSync.compute_file_hash(temp_path)
            assert hash1 == hash2
        finally:
            os.unlink(temp_path)


class TestRemoteEnvConfig:
    """RemoteEnvConfig测试"""

    def test_host_environment(self):
        """测试宿主机环境"""
        config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
        )
        assert config.get_environment_type() == EnvironmentType.HOST
        assert "宿主机环境" in config.get_environment_description()

    def test_auto_container_environment(self):
        """测试自动创建容器环境"""
        config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
            image_name="ascend/pytorch:23.0",
        )
        assert config.get_environment_type() == EnvironmentType.AUTO_CONTAINER
        assert "自动创建容器" in config.get_environment_description()
        assert "ascend/pytorch:23.0" in config.get_environment_description()

    def test_existing_container_environment(self):
        """测试已有容器环境"""
        config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
            image_name="ascend/pytorch:23.0",
            container_name="agent-dev",
        )
        assert config.get_environment_type() == EnvironmentType.EXISTING_CONTAINER
        assert "容器内开发" in config.get_environment_description()
        assert "agent-dev" in config.get_environment_description()

    def test_requires_confirmation(self):
        """测试需要确认"""
        config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
        )
        assert config.requires_confirmation() is True

    def test_to_environment_info(self):
        """测试转换为EnvironmentInfo"""
        config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
            port=22,
            image_name="ascend/pytorch:23.0",
            container_name="agent-dev",
        )
        info = config.to_environment_info()

        assert info.type == EnvironmentType.EXISTING_CONTAINER
        assert info.host == "192.168.1.100"
        assert info.user == "root"
        assert info.port == 22
        assert info.image_name == "ascend/pytorch:23.0"
        assert info.container_name == "agent-dev"


class TestEnvironmentType:
    """EnvironmentType测试"""

    def test_enum_values(self):
        """测试枚举值"""
        assert EnvironmentType.HOST.value == "host"
        assert EnvironmentType.AUTO_CONTAINER.value == "auto_container"
        assert EnvironmentType.EXISTING_CONTAINER.value == "existing_container"
