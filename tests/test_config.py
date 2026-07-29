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

"""配置模块测试"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from ascend_op_agent.config import (
    Config,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    LocalConfig,
    LoggingConfig,
    RemoteConfig,
    load_config,
)


class TestLLMConfig:
    """LLM 配置测试"""

    def test_default_values(self):
        config = LLMConfig()
        assert config.provider == "openai"
        assert config.api_base == "https://api.openai.com/v1"
        assert config.model == "gpt-4o"
        assert config.max_retries == 3
        assert config.timeout == 120

    def test_custom_values(self):
        config = LLMConfig(
            provider="anthropic",
            api_base="https://api.anthropic.com",
            model="claude-3-opus",
            max_retries=5,
            timeout=60,
        )
        assert config.provider == "anthropic"
        assert config.api_base == "https://api.anthropic.com"
        assert config.model == "claude-3-opus"
        assert config.max_retries == 5
        assert config.timeout == 60


class TestRemoteConfig:
    """远程配置测试"""

    def test_environment_type_host_only(self):
        """无镜像无容器 - 宿主机环境"""
        config = RemoteConfig(host="192.168.1.100", user="root")
        assert config.get_environment_type() == "宿主机环境开发"

    def test_environment_type_auto_container(self):
        """有镜像无容器 - 自动创建容器"""
        config = RemoteConfig(
            host="192.168.1.100",
            user="root",
            image_name="ascend/pytorch:23.0",
        )
        assert "自动创建容器" in config.get_environment_type()
        assert "ascend/pytorch:23.0" in config.get_environment_type()

    def test_environment_type_existing_container(self):
        """有镜像有容器 - 容器内开发"""
        config = RemoteConfig(
            host="192.168.1.100",
            user="root",
            image_name="ascend/pytorch:23.0",
            container_name="agent-dev",
        )
        assert "容器内开发" in config.get_environment_type()
        assert "agent-dev" in config.get_environment_type()

    def test_requires_confirmation(self):
        config = RemoteConfig(host="192.168.1.100", user="root")
        assert config.requires_confirmation() is True


class TestConfig:
    """主配置类测试"""

    def test_default_config(self):
        config = Config()
        assert config.llm is not None
        assert config.local is not None
        assert config.logging is not None

    def test_save_and_load(self):
        """测试配置保存和加载"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config_path = f.name

        try:
            original_config = Config(
                llm=LLMConfig(provider="test", model="test-model"),
                local=LocalConfig(workspace="/tmp/test"),
            )
            original_config.save(config_path)

            loaded_config = Config.from_file(config_path)
            assert loaded_config.llm.provider == "test"
            assert loaded_config.llm.model == "test-model"
            assert loaded_config.local.workspace == "/tmp/test"
        finally:
            os.unlink(config_path)

    def test_env_var_resolution(self):
        """测试 ${ENV_VAR} 环境变量解析"""
        os.environ["TEST_API_KEY"] = "secret-key-123"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"llm": {"api_key": "${TEST_API_KEY}"}}, f)
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            assert config.llm.api_key == "secret-key-123"
        finally:
            os.unlink(config_path)
            del os.environ["TEST_API_KEY"]

    def test_nested_env_var_resolution(self):
        """测试嵌套配置的 ${ENV_VAR} 解析"""
        os.environ["REMOTE_HOST"] = "192.168.1.200"
        os.environ["REMOTE_USER"] = "admin"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(
                {"remote": {"host": "${REMOTE_HOST}", "user": "${REMOTE_USER}", "port": 22}}, f
            )
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            assert config.remote.host == "192.168.1.200"
            assert config.remote.user == "admin"
        finally:
            os.unlink(config_path)
            del os.environ["REMOTE_HOST"]
            del os.environ["REMOTE_USER"]

    def test_missing_env_var_resolves_to_empty(self):
        """测试缺失的环境变量解析为空字符串"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"llm": {"api_key": "${NON_EXISTENT_VAR_12345}"}}, f)
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            assert config.llm.api_key == ""
        finally:
            os.unlink(config_path)


class TestLoadConfig:
    """load_config 便捷函数测试"""

    def test_load_default_config_path_not_exists(self):
        """默认配置不存在时返回默认配置"""
        # 指定不存在的路径，返回默认配置
        config = load_config("/non/existent/path.yaml")
        assert config is not None
        assert isinstance(config, Config)

    def test_load_specific_path(self):
        """加载指定路径的配置"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"llm": {"provider": "custom"}}, f)
            config_path = f.name

        try:
            config = load_config(config_path)
            assert config.llm.provider == "custom"
        finally:
            os.unlink(config_path)
