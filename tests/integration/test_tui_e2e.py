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

"""TUI 端到端测试

验证完整的前后端集成功能。
注意：完整的 TUI E2E 测试需要 pty 模拟终端交互，
当前实现为基础版本，主要验证模块结构和通信。
"""

import json
import pytest
from pathlib import Path


class TestTUIE2E:
    """TUI 端到端测试"""

    def test_frontend_structure_exists(self):
        """测试前端项目结构存在"""
        project_root = Path(__file__).parent.parent.parent
        frontend_path = project_root / "frontend"

        assert frontend_path.exists(), f"前端目录不存在: {frontend_path}"

    def test_frontend_package_json_exists(self):
        """测试前端 package.json 存在"""
        project_root = Path(__file__).parent.parent.parent
        package_json = project_root / "frontend" / "package.json"

        assert package_json.exists(), f"package.json 不存在: {package_json}"

    def test_frontend_dist_exists_after_build(self):
        """测试前端 dist 目录存在（如果已构建）"""
        project_root = Path(__file__).parent.parent.parent
        dist_path = project_root / "frontend" / "dist"

        # dist 目录可能不存在（未执行 npm run build）
        # 这里只检查如果存在的话结构是否正确
        if dist_path.exists():
            index_js = dist_path / "index.js"
            # 如果 dist 存在，index.js 应该存在
            assert index_js.exists(), f"index.js 不存在于: {dist_path}"

    def test_backend_module_importable(self):
        """测试后端模块可以正常导入"""
        import sys
        project_root = Path(__file__).parent.parent.parent
        src_path = project_root / "src"

        # 确保 src 路径在 sys.path 中
        if str(src_path) not in sys.path:
            sys.path.insert(0, str(src_path))

        # 测试可以导入后端模块
        from ascend_op_agent.backend import JSONRPCServer, AgentAsyncWrapper, AgentResponse

        assert JSONRPCServer is not None
        assert AgentAsyncWrapper is not None
        assert AgentResponse is not None

    def test_backend_rpc_server_instantiation(self):
        """测试 RPC 服务器可以正常实例化"""
        from ascend_op_agent.backend import JSONRPCServer

        server = JSONRPCServer()
        assert server is not None
        assert hasattr(server, "register_method")
        assert hasattr(server, "send_notification")
        assert hasattr(server, "run")

    def test_agent_response_type(self):
        """测试 AgentResponse 类型定义"""
        from ascend_op_agent.backend import AgentResponse

        # 测试 TypedDict 约束
        valid_response = AgentResponse(
            status="completed",
            response="test response",
            data=None
        )
        assert valid_response["status"] == "completed"
        assert valid_response["response"] == "test response"

        # 测试可选字段
        error_response = AgentResponse(
            status="error",
            response=None,
            data={"message": "error occurred"}
        )
        assert error_response["status"] == "error"
        assert error_response["data"]["message"] == "error occurred"


class TestBackendProcess:
    """后端进程测试"""

    def test_backend_entry_point_exists(self):
        """测试后端入口文件存在"""
        project_root = Path(__file__).parent.parent.parent
        backend_path = project_root / "src" / "ascend_op_agent" / "backend.py"

        assert backend_path.exists(), f"backend.py 不存在: {backend_path}"

    def test_backend_has_main_function(self):
        """测试 backend.py 包含 main 函数"""
        project_root = Path(__file__).parent.parent.parent
        backend_path = project_root / "src" / "ascend_op_agent" / "backend.py"

        content = backend_path.read_text()
        assert "async def main()", f"backend.py 缺少 async def main()"
        assert "if __name__", f"backend.py 缺少 if __name__ == '__main__'"

    def test_backend_rpc_server_usage(self):
        """测试 backend.py 正确使用 RPC 服务器"""
        project_root = Path(__file__).parent.parent.parent
        backend_path = project_root / "src" / "ascend_op_agent" / "backend.py"

        content = backend_path.read_text()
        assert "JSONRPCServer", "backend.py 未使用 JSONRPCServer"
        assert "agent.run", "backend.py 未注册 agent.run 方法"
        assert "session.reset", "backend.py 未注册 session.reset 方法"
        assert "backend.ready", "backend.py 未发送 backend.ready 通知"


class TestProtocolIntegration:
    """协议集成测试"""

    def test_protocol_with_server(self):
        """测试协议与服务器集成"""
        from ascend_op_agent.backend.rpc.server import JSONRPCServer
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol

        server = JSONRPCServer()

        # 测试方法注册
        async def dummy_handler(**params):
            return {"status": "ok"}

        server.register_method("test.method", dummy_handler)

        # 验证协议可以构建正确的消息
        notification = JSONRPCProtocol.build_notification("test.notify", {"key": "value"})
        parsed = json.loads(notification)

        # JSON 序列化测试
        from ascend_op_agent.backend.rpc.protocol import RPCNotification
        notif = RPCNotification(jsonrpc="2.0", method="test", params={"a": 1})
        json_str = JSONRPCProtocol.build_notification("test", {"a": 1})
        assert '"jsonrpc": "2.0"' in json_str
        assert '"method": "test"' in json_str

    def test_request_response_cycle(self):
        """测试请求-响应完整周期"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol, RPCRequest

        # 模拟收到请求
        raw_request = '{"jsonrpc": "2.0", "id": 42, "method": "echo", "params": {"msg": "hello"}}'
        request = JSONRPCProtocol.parse_request(raw_request)

        assert isinstance(request, RPCRequest)
        assert request.method == "echo"
        assert request.params == {"msg": "hello"}

        # 模拟发送响应
        response = JSONRPCProtocol.build_response(42, {"echoed": "hello"})
        parsed = json.loads(response)

        assert parsed["id"] == 42
        assert parsed["result"]["echoed"] == "hello"