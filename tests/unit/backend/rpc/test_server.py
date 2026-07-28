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

"""JSON-RPC Server 单元测试"""

import asyncio
import json
import pytest
import pytest_asyncio

from ascend_op_agent.backend.rpc.protocol import (
    JSONRPCProtocol,
    JSONRPCParseError,
    RPCRequest,
    RPCNotification,
)
from ascend_op_agent.backend.rpc.server import JSONRPCServer


class TestJSONRPCProtocol:
    """JSON-RPC 协议测试"""

    def test_parse_request_with_id(self):
        """测试解析带 ID 的请求"""
        raw = '{"jsonrpc": "2.0", "id": 1, "method": "test.method", "params": {"key": "value"}}'
        result = JSONRPCProtocol.parse_request(raw)

        assert isinstance(result, RPCRequest)
        assert result.id == 1
        assert result.method == "test.method"
        assert result.params == {"key": "value"}

    def test_parse_notification_without_id(self):
        """测试解析无 ID 的通知"""
        raw = '{"jsonrpc": "2.0", "method": "notify", "params": {"foo": "bar"}}'
        result = JSONRPCProtocol.parse_request(raw)

        assert isinstance(result, RPCNotification)
        assert result.id is None
        assert result.method == "notify"
        assert result.params == {"foo": "bar"}

    def test_parse_empty_request_raises_error(self):
        """测试解析空请求抛出错误"""
        with pytest.raises(JSONRPCParseError):
            JSONRPCProtocol.parse_request("")

    def test_parse_invalid_json_raises_error(self):
        """测试解析无效 JSON 抛出错误"""
        with pytest.raises(JSONRPCParseError) as exc_info:
            JSONRPCProtocol.parse_request("not valid json")
        assert exc_info.value.code == JSONRPCProtocol.PARSE_ERROR_CODE

    def test_parse_invalid_version_raises_error(self):
        """测试解析无效版本抛出错误"""
        with pytest.raises(JSONRPCParseError) as exc_info:
            JSONRPCProtocol.parse_request('{"jsonrpc": "1.0", "method": "test"}')
        assert exc_info.value.code == JSONRPCProtocol.INVALID_REQUEST_CODE

    def test_parse_missing_method_raises_error(self):
        """测试解析缺少 method 字段抛出错误"""
        with pytest.raises(JSONRPCParseError) as exc_info:
            JSONRPCProtocol.parse_request('{"jsonrpc": "2.0", "id": 1}')
        assert exc_info.value.code == JSONRPCProtocol.INVALID_REQUEST_CODE

    def test_build_response(self):
        """测试构建成功响应"""
        response = JSONRPCProtocol.build_response(id=1, result={"status": "ok"})
        data = json.loads(response)

        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["result"] == {"status": "ok"}

    def test_build_error(self):
        """测试构建错误响应"""
        response = JSONRPCProtocol.build_error(id=1, code=-32601, message="Method not found")
        data = json.loads(response)

        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert data["error"]["code"] == -32601
        assert data["error"]["message"] == "Method not found"

    def test_build_notification(self):
        """测试构建通知"""
        notification = JSONRPCProtocol.build_notification(
            method="agent.progress", params={"percent": 50}
        )
        data = json.loads(notification)

        assert data["jsonrpc"] == "2.0"
        assert data["method"] == "agent.progress"
        assert data["params"] == {"percent": 50}


class TestJSONRPCServer:
    """JSON-RPC 服务端测试"""

    @pytest_asyncio.fixture
    async def server(self):
        """创建测试服务器"""
        return JSONRPCServer()

    def test_register_method(self, server):
        """测试注册方法"""

        def handler(**params):
            return {"status": "ok"}

        server.register_method("test.handler", handler)
        assert "test.handler" in server._methods

    @pytest.mark.asyncio
    async def test_send_notification(self, server, capsys):
        """测试发送通知"""
        server._running = True
        await server.send_notification("test.notify", {"key": "value"})

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())

        assert output["jsonrpc"] == "2.0"
        assert output["method"] == "test.notify"
        assert output["params"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_method_not_found(self, server, capsys):
        """测试调用未注册方法返回错误"""
        server._running = True
        await server._handle_message(
            '{"jsonrpc": "2.0", "id": 1, "method": "nonexistent", "params": {}}'
        )

        captured = capsys.readouterr()
        error = json.loads(captured.out.strip())

        assert error["error"]["code"] == JSONRPCProtocol.METHOD_NOT_FOUND_CODE

    @pytest.mark.asyncio
    async def test_sync_handler(self, server, capsys):
        """测试同步处理函数"""

        def sync_handler(**params):
            return {"result": params.get("input")}

        server.register_method("sync.call", sync_handler)
        server._running = True

        await server._handle_message(
            '{"jsonrpc": "2.0", "id": 2, "method": "sync.call", "params": {"input": "test"}}'
        )

        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())

        assert response["id"] == 2
        assert response["result"]["result"] == "test"

    @pytest.mark.asyncio
    async def test_async_handler(self, server, capsys):
        """测试异步处理函数"""

        async def async_handler(**params):
            await asyncio.sleep(0.01)
            return {"result": params.get("input")}

        server.register_method("async.call", async_handler)
        server._running = True

        await server._handle_message(
            '{"jsonrpc": "2.0", "id": 3, "method": "async.call", "params": {"input": "async_test"}}'
        )

        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())

        assert response["id"] == 3
        assert response["result"]["result"] == "async_test"

    @pytest.mark.asyncio
    async def test_handler_exception(self, server, capsys):
        """测试处理函数异常时返回错误响应"""

        def failing_handler(**params):
            raise ValueError("Test error")

        server.register_method("fail.call", failing_handler)
        server._running = True

        await server._handle_message(
            '{"jsonrpc": "2.0", "id": 4, "method": "fail.call", "params": {}}'
        )

        captured = capsys.readouterr()
        error = json.loads(captured.out.strip())

        assert error["id"] == 4
        assert error["error"]["code"] == JSONRPCProtocol.INTERNAL_ERROR_CODE
        assert "Test error" in error["error"]["message"]
