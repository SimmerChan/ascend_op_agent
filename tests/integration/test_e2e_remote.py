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

"""远程模式端到端集成测试

测试远程开发模式相关功能。
"""

import tempfile
from pathlib import Path

import pytest


class TestRemoteEnvConfig:
    """远程环境配置测试"""

    def test_remote_env_config_parsing(self):
        """测试远程环境配置解析"""
        from ascend_op_agent.ssh.env_config import RemoteEnvConfig

        env_config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
            port=22,
            key_path="~/.ssh/id_rsa",
            password="${SSH_PASSWORD}",
            image_name="ascend/pytorch:23.0",
            container_name="agent-dev",
        )

        assert env_config.host == "192.168.1.100"
        assert env_config.image_name == "ascend/pytorch:23.0"
        assert env_config.container_name == "agent-dev"

    def test_remote_env_config_without_container(self):
        """测试无容器配置解析"""
        from ascend_op_agent.ssh.env_config import RemoteEnvConfig

        env_config = RemoteEnvConfig(
            host="192.168.1.100",
            user="root",
        )

        assert env_config.host == "192.168.1.100"
        assert env_config.image_name is None
        assert env_config.container_name is None


class TestRemoteWorkflowContext:
    """远程工作流上下文测试"""

    def test_remote_context_structure(self):
        """测试远程上下文结构"""
        from ascend_op_agent.workflow.models import OpInfo

        op_info = OpInfo(
            name="test_op",
            description="Test remote operator",
            op_type="elementwise",
        )

        context = {
            "op_info": op_info,
            "remote_config": {
                "host": "192.168.1.100",
                "user": "root",
            },
            "local_workspace": "/tmp/local",
            "remote_workspace": "/tmp/remote",
        }

        assert context["op_info"].name == "test_op"
        assert context["remote_config"]["host"] == "192.168.1.100"


class TestCredentialManagerIntegration:
    """凭据管理器集成测试"""

    def test_env_var_credential_resolution(self):
        """测试环境变量凭据解析"""
        import os

        os.environ["TEST_MCP_TOKEN"] = "mcp-token-123"

        from ascend_op_agent.security.token_resolver import TokenResolver

        resolver = TokenResolver()

        token = resolver.get_bearer_token("${TEST_MCP_TOKEN}")

        assert token == "Bearer mcp-token-123"

        del os.environ["TEST_MCP_TOKEN"]

    def test_credential_manager_ssh_password(self):
        """测试SSH密码凭据管理"""
        import os

        os.environ["TEST_SSH_PASS"] = "my-secret-password"

        from ascend_op_agent.security.credential_manager import CredentialManager

        manager = CredentialManager()

        resolved = manager.get_ssh_password("${TEST_SSH_PASS}", "192.168.1.100")
        assert resolved == "my-secret-password"

        del os.environ["TEST_SSH_PASS"]
