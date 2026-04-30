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

"""安全模块测试"""

import os

import pytest

from ascend_op_agent.security.credential_manager import CredentialManager
from ascend_op_agent.security.token_resolver import TokenResolver


class TestCredentialManager:
    """CredentialManager测试"""

    def test_resolve_direct_value(self):
        """测试直接值"""
        manager = CredentialManager()
        result = manager.resolve_env_var("direct_value")
        assert result == "direct_value"

    def test_resolve_empty_value(self):
        """测试空值"""
        manager = CredentialManager()
        result = manager.resolve_env_var("")
        assert result == ""

        result = manager.resolve_env_var(None)
        assert result is None

    def test_resolve_env_var_reference(self):
        """测试环境变量引用"""
        os.environ["TEST_VAR"] = "test_value"

        manager = CredentialManager()
        result = manager.resolve_env_var("${TEST_VAR}")
        assert result == "test_value"

        del os.environ["TEST_VAR"]

    def test_resolve_multiple_env_vars(self):
        """测试多个环境变量引用"""
        os.environ["VAR1"] = "value1"
        os.environ["VAR2"] = "value2"

        manager = CredentialManager()
        result = manager.resolve_env_var("${VAR1}:${VAR2}")
        assert result == "value1:value2"

        del os.environ["VAR1"]
        del os.environ["VAR2"]

    def test_resolve_nonexistent_env_var(self):
        """测试不存在的环境变量"""
        manager = CredentialManager()
        result = manager.resolve_env_var("${NON_EXISTENT_VAR_123}")
        assert result == ""

    def test_get_ssh_password_direct(self):
        """测试直接获取密码"""
        manager = CredentialManager()
        result = manager.get_ssh_password("secret_password")
        assert result == "secret_password"

    def test_get_ssh_password_with_env_var(self):
        """测试通过环境变量获取密码"""
        os.environ["SSH_PASS"] = "env_password"

        manager = CredentialManager()
        result = manager.get_ssh_password("${SSH_PASS}")
        assert result == "env_password"

        del os.environ["SSH_PASS"]

    def test_has_env_var_reference(self):
        """测试检查环境变量引用"""
        manager = CredentialManager()
        assert manager.has_env_var_reference("${VAR}") is True
        assert manager.has_env_var_reference("plain_text") is False
        assert manager.has_env_var_reference("") is False
        assert manager.has_env_var_reference(None) is False

    def test_extract_env_vars(self):
        """测试提取环境变量"""
        manager = CredentialManager()
        vars_list = manager.extract_env_vars("${VAR1} and ${VAR2}")
        assert vars_list == ["VAR1", "VAR2"]

    def test_extract_env_vars_none(self):
        """测试提取空值的环境变量"""
        manager = CredentialManager()
        vars_list = manager.extract_env_vars("plain_text")
        assert vars_list == []


class TestTokenResolver:
    """TokenResolver测试"""

    def test_resolve_token_direct(self):
        """测试直接Token"""
        resolver = TokenResolver()
        result = resolver.resolve_token("bearer_token")
        assert result == "bearer_token"

    def test_resolve_token_empty(self):
        """测试空Token"""
        resolver = TokenResolver()
        # 空字符串返回None或空字符串
        result = resolver.resolve_token("")
        assert result == "" or result is None

        result = resolver.resolve_token(None)
        assert result is None

    def test_resolve_token_with_env_var(self):
        """测试环境变量Token"""
        os.environ["MCP_TOKEN"] = "secret_token"

        resolver = TokenResolver()
        result = resolver.resolve_token("${MCP_TOKEN}")
        assert result == "secret_token"

        del os.environ["MCP_TOKEN"]

    def test_get_bearer_token(self):
        """测试获取Bearer Token"""
        resolver = TokenResolver()
        result = resolver.get_bearer_token("my_token")
        assert result == "Bearer my_token"

    def test_get_bearer_token_empty(self):
        """测试空Bearer Token"""
        resolver = TokenResolver()
        result = resolver.get_bearer_token("")
        assert result is None

        result = resolver.get_bearer_token(None)
        assert result is None

    def test_get_auth_header(self):
        """测试获取认证头"""
        resolver = TokenResolver()
        result = resolver.get_auth_header("token123")
        assert result == {"Authorization": "Bearer token123"}

    def test_get_auth_header_empty(self):
        """测试空认证头"""
        resolver = TokenResolver()
        result = resolver.get_auth_header("")
        assert result is None

    def test_has_env_var_reference(self):
        """测试检查环境变量引用"""
        resolver = TokenResolver()
        assert resolver.has_env_var_reference("${TOKEN_VAR}") is True
        assert resolver.has_env_var_reference("plain_token") is False
        assert resolver.has_env_var_reference("") is False
        assert resolver.has_env_var_reference(None) is False

    def test_extract_env_vars(self):
        """测试提取环境变量"""
        resolver = TokenResolver()
        vars_list = resolver.extract_env_vars("${TOKEN1} and ${TOKEN2}")
        assert vars_list == ["TOKEN1", "TOKEN2"]
