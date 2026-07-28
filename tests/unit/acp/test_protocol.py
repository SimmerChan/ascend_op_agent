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

"""ACP 协议处理器单元测试"""

import pytest
from ascend_op_agent.acp.protocol import ACPProtocol


class TestACPProtocol:
    """ACP 协议处理器测试"""

    def setup_method(self):
        """每个测试方法前的设置"""
        self.protocol = ACPProtocol()

    def test_initialize_method_defined(self):
        """测试 initialize 方法已定义"""
        assert ACPProtocol.INITIALIZE == "initialize"

    def test_agent_run_method_defined(self):
        """测试 agent.run 方法已定义"""
        assert ACPProtocol.AGENT_RUN == "agent.run"

    def test_tools_list_method_defined(self):
        """测试 tools/list 方法已定义"""
        assert ACPProtocol.TOOLS_LIST == "tools/list"

    def test_tools_call_method_defined(self):
        """测试 tools/call 方法已定义"""
        assert ACPProtocol.TOOLS_CALL == "tools/call"

    def test_acp_methods_whitelist(self):
        """测试 ACP 方法白名单"""
        assert "initialize" in ACPProtocol.ACP_METHODS
        assert "agent.run" in ACPProtocol.ACP_METHODS
        assert "tools/list" in ACPProtocol.ACP_METHODS
        assert "tools/call" in ACPProtocol.ACP_METHODS

    def test_validate_method_valid(self):
        """测试有效方法验证"""
        assert self.protocol.validate_method("initialize") is True
        assert self.protocol.validate_method("agent.run") is True
        assert self.protocol.validate_method("tools/list") is True
        assert self.protocol.validate_method("tools/call") is True

    def test_validate_method_invalid(self):
        """测试无效方法验证"""
        assert self.protocol.validate_method("unknown.method") is False
        assert self.protocol.validate_method("exec") is False
        assert self.protocol.validate_method("") is False

    def test_validate_params_initialize(self):
        """测试 initialize 参数验证"""
        valid_params = {"clientInfo": {"name": "test"}, "capabilities": {}}
        assert self.protocol.validate_params("initialize", valid_params) is True
        assert self.protocol.validate_params("initialize", None) is True
        assert self.protocol.validate_params("initialize", {"invalid": "data"}) is True  # dict 即可

    def test_validate_params_agent_run(self):
        """测试 agent.run 参数验证"""
        valid_params = {"user_input": "hello"}
        assert self.protocol.validate_params("agent.run", valid_params) is True
        assert self.protocol.validate_params("agent.run", {"user_input": ""}) is True
        assert self.protocol.validate_params("agent.run", {}) is False  # 缺少 user_input
        assert self.protocol.validate_params("agent.run", None) is True  # None 视为有效

    def test_validate_params_tools_call(self):
        """测试 tools/call 参数验证"""
        valid_params = {"name": "bash", "args": {"command": "ls"}}
        assert self.protocol.validate_params("tools/call", valid_params) is True
        assert self.protocol.validate_params("tools/call", {"name": "bash"}) is False  # 缺少 args
        assert self.protocol.validate_params("tools/call", {"args": {}}) is False  # 缺少 name

    def test_build_initialize_response(self):
        """测试构建 initialize 响应"""
        capabilities = {"supports": ["initialize"]}
        response = self.protocol.build_initialize_response(capabilities)

        assert '"protocolVersion": "1.0"' in response
        assert '"capabilities"' in response
        assert '"serverInfo"' in response

    def test_build_error_response(self):
        """测试构建错误响应"""
        response = self.protocol.build_error_response(1, -32601, "Method not found")

        assert '"error"' in response
        assert '"code": -32601' in response or '"code":-32601' in response.replace(" ", "")

    def test_build_success_response(self):
        """测试构建成功响应"""
        response = self.protocol.build_success_response(1, {"result": "success"})

        assert '"result"' in response

    def test_build_notification(self):
        """测试构建通知消息"""
        notification = self.protocol.build_notification("backend.ready", {})

        assert '"jsonrpc": "2.0"' in notification
        assert '"method": "backend.ready"' in notification

    def test_get_method_info(self):
        """测试获取方法信息"""
        info = self.protocol.get_method_info("initialize")

        assert info["description"] == "初始化会话，传递客户端能力"
        assert info["direction"] == "Editor→Agent"

    def test_get_method_info_unknown(self):
        """测试获取未知方法信息"""
        info = self.protocol.get_method_info("unknown.method")

        assert info == {}
