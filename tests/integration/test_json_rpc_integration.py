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

"""JSON-RPC 协议集成测试

验证完整双进程架构的 JSON-RPC 通信功能。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest


class TestJSONRPCIntegration:
    """JSON-RPC 协议集成测试"""

    @pytest.fixture
    def backend_process(self):
        """启动后端进程"""
        project_root = Path(__file__).parent.parent.parent
        backend_path = project_root / "src" / "ascend_op_agent" / "backend.py"

        # 确保项目根目录在 Python 路径中
        env = {
            "PYTHONUNBUFFERED": "1",
            "ASCEND_OP_AGENT_CONFIG": "",
            "PYTHONPATH": str(project_root / "src"),
        }

        process = subprocess.Popen(
            [sys.executable, str(backend_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(project_root),
        )

        yield process

        # 清理：终止后端进程
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _read_until_ready(self, process, timeout=10):
        """读取输出直到收到 backend.ready 通知

        Args:
            process: 子进程
            timeout: 超时时间（秒）

        Returns:
            后端就绪后的输出行列表
        """
        start = time.time()
        lines = []
        while time.time() - start < timeout:
            line = process.stdout.readline()
            if not line:
                # EOF 或管道关闭
                break
            lines.append(line)
            msg = json.loads(line)
            if msg.get("method") == "backend.ready":
                return lines
        return lines

    def _send_request(self, process, method: str, params=None, req_id=1):
        """发送 JSON-RPC 请求

        Args:
            process: 子进程
            method: 方法名
            params: 参数字典
            req_id: 请求 ID

        Returns:
            发送的请求字典
        """
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        process.stdin.flush()
        return request

    def _read_response(self, process, req_id, timeout=30):
        """读取指定 ID 的响应

        Args:
            process: 子进程
            req_id: 请求 ID
            timeout: 超时时间（秒）

        Returns:
            响应字典

        Raises:
            pytest.fail: 超时未收到响应
        """
        start = time.time()
        while time.time() - start < timeout:
            line = process.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            # 检查是否是目标请求的响应（id 匹配）
            if msg.get("id") == req_id:
                return msg
        pytest.fail(f"未收到请求 {req_id} 的响应")

    def test_backend_ready_notification(self, backend_process):
        """测试后端就绪通知"""
        lines = self._read_until_ready(backend_process, timeout=10)

        # 验证收到了 backend.ready 通知
        found_ready = False
        for line in lines:
            msg = json.loads(line)
            if msg.get("method") == "backend.ready":
                found_ready = True
                assert msg["jsonrpc"] == "2.0"
                break

        assert found_ready, f"未收到 backend.ready 通知，收到消息: {[json.loads(l) for l in lines]}"

    def test_single_request_response(self, backend_process):
        """测试单个请求-响应"""
        # 等待后端就绪
        self._read_until_ready(backend_process, timeout=10)

        # 发送 agent.run 请求
        request = self._send_request(
            backend_process, "agent.run", {"user_input": "hello"}, req_id=1
        )

        # 读取响应
        response = self._read_response(backend_process, req_id=1, timeout=30)
        assert response["jsonrpc"] == "2.0"
        assert "result" in response or "error" in response

    def test_session_reset(self, backend_process):
        """测试会话重置"""
        # 等待后端就绪
        self._read_until_ready(backend_process, timeout=10)

        # 发送 session.reset 请求
        self._send_request(backend_process, "session.reset", {}, req_id=2)

        # 读取响应
        response = self._read_response(backend_process, req_id=2, timeout=10)
        assert response["jsonrpc"] == "2.0"

    def test_invalid_request(self, backend_process):
        """测试无效请求处理"""
        # 等待后端就绪
        self._read_until_ready(backend_process, timeout=10)

        # 发送无效请求（方法名不存在）
        invalid_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "nonexistent.method",
            "params": {},
        }
        backend_process.stdin.write((json.dumps(invalid_request) + "\n").encode("utf-8"))
        backend_process.stdin.flush()

        # 读取错误响应
        response = self._read_response(backend_process, req_id=3, timeout=5)
        assert response["jsonrpc"] == "2.0"
        assert "error" in response
        assert response["error"]["code"] == -32601  # METHOD_NOT_FOUND

    def test_multiple_requests_sequential(self, backend_process):
        """测试多个请求顺序处理"""
        # 等待后端就绪
        self._read_until_ready(backend_process, timeout=10)

        # 发送多个请求
        for i in range(4, 7):
            self._send_request(backend_process, "session.reset", {}, req_id=i)

        # 顺序读取响应
        for i in range(4, 7):
            response = self._read_response(backend_process, req_id=i, timeout=10)
            assert response["jsonrpc"] == "2.0"
            assert "result" in response or "error" in response


class TestJSONRPCProtocol:
    """JSON-RPC 协议细节测试（无需启动真实后端）"""

    def test_valid_request_parsing(self):
        """测试有效请求解析"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol, RPCRequest

        raw = '{"jsonrpc": "2.0", "id": 1, "method": "test.method", "params": {"key": "value"}}'
        result = JSONRPCProtocol.parse_request(raw)

        assert isinstance(result, RPCRequest)
        assert result.method == "test.method"
        assert result.id == 1
        assert result.params == {"key": "value"}

    def test_notification_parsing(self):
        """测试通知解析"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol, RPCNotification

        raw = '{"jsonrpc": "2.0", "method": "backend.ready", "params": {}}'
        result = JSONRPCProtocol.parse_request(raw)

        assert isinstance(result, RPCNotification)
        assert result.method == "backend.ready"
        assert result.params == {}

    def test_invalid_json_parsing(self):
        """测试无效 JSON 解析"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol, JSONRPCParseError

        with pytest.raises(JSONRPCParseError) as exc_info:
            JSONRPCProtocol.parse_request("not valid json")
        assert exc_info.value.code == -32700

    def test_missing_method_parsing(self):
        """测试缺少 method 字段的请求解析"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol, JSONRPCParseError

        with pytest.raises(JSONRPCParseError):
            JSONRPCProtocol.parse_request('{"jsonrpc": "2.0", "id": 1}')

    def test_response_building(self):
        """测试响应构建"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol

        response = JSONRPCProtocol.build_response(1, {"status": "ok"})
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert parsed["result"] == {"status": "ok"}

    def test_error_response_building(self):
        """测试错误响应构建"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol

        response = JSONRPCProtocol.build_error(1, -32600, "Invalid Request")
        parsed = json.loads(response)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert parsed["error"]["code"] == -32600
        assert parsed["error"]["message"] == "Invalid Request"

    def test_notification_building(self):
        """测试通知构建"""
        from ascend_op_agent.backend.rpc.protocol import JSONRPCProtocol

        notification = JSONRPCProtocol.build_notification("agent.thinking", {"message": "thinking..."})
        parsed = json.loads(notification)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "agent.thinking"
        assert parsed["params"] == {"message": "thinking..."}