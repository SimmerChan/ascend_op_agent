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

"""JSON-RPC 2.0 协议核心实现

提供请求/响应解析和构建功能。
"""

import json
from dataclasses import dataclass
from typing import Any, Optional, Union


@dataclass
class RPCRequest:
    """JSON-RPC 2.0 请求对象"""
    jsonrpc: str = "2.0"
    method: str = ""
    id: Any = None
    params: Optional[dict[str, Any]] = None


@dataclass
class RPCNotification:
    """JSON-RPC 2.0 通知对象（无响应）"""
    jsonrpc: str = "2.0"
    method: str = ""
    id: Any = None  # Notification has no id, but we track it for consistency
    params: Optional[dict[str, Any]] = None


@dataclass
class RPCResponse:
    """JSON-RPC 2.0 响应对象"""
    jsonrpc: str = "2.0"
    id: Any = None
    result: Optional[Any] = None
    error: Optional[dict[str, Any]] = None


class JSONRPCParseError(Exception):
    """JSON-RPC 解析错误"""
    def __init__(self, message: str, code: int = -32700):
        self.message = message
        self.code = code
        super().__init__(message)


class JSONRPCProtocol:
    """JSON-RPC 2.0 协议处理器"""

    # 错误代码
    PARSE_ERROR_CODE = -32700
    INVALID_REQUEST_CODE = -32600
    METHOD_NOT_FOUND_CODE = -32601
    INVALID_PARAMS_CODE = -32602
    INTERNAL_ERROR_CODE = -32603

    @classmethod
    def parse_request(cls, raw: str) -> Union[RPCRequest, RPCNotification]:
        """解析 JSON-RPC 请求或通知

        Args:
            raw: JSON 字符串

        Returns:
            RPCRequest 或 RPCNotification 对象

        Raises:
            JSONRPCParseError: 解析失败时抛出
        """
        if not raw or not raw.strip():
            raise JSONRPCParseError("Empty request", cls.PARSE_ERROR_CODE)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise JSONRPCParseError(f"Invalid JSON: {e}", cls.PARSE_ERROR_CODE)

        if not isinstance(data, dict):
            raise JSONRPCParseError("Request must be a JSON object", cls.INVALID_REQUEST_CODE)

        if data.get("jsonrpc") != "2.0":
            raise JSONRPCParseError(
                "Invalid jsonrpc version, expected '2.0'",
                cls.INVALID_REQUEST_CODE
            )

        if "method" not in data:
            raise JSONRPCParseError("Missing 'method' field", cls.INVALID_REQUEST_CODE)

        method = data.get("method", "")
        params = data.get("params")
        rpc_id = data.get("id")

        # 有 id 的是 request，无 id 的是 notification
        if rpc_id is not None:
            return RPCRequest(
                jsonrpc="2.0",
                method=method,
                id=rpc_id,
                params=params
            )
        else:
            return RPCNotification(
                jsonrpc="2.0",
                method=method,
                params=params
            )

    @classmethod
    def build_response(cls, id: Any, result: Any) -> str:
        """构建 JSON-RPC 成功响应

        Args:
            id: 请求 ID
            result: 响应结果

        Returns:
            JSON 字符串
        """
        # P1 U4 fix: 直接构造 dict, 避免 RPCResponse.__dict__ 包含 None 字段 (如 error=null)
        return json.dumps(
            {"jsonrpc": "2.0", "id": id, "result": result},
            ensure_ascii=False,
        )

    @classmethod
    def build_error(cls, id: Any, code: int, message: str) -> str:
        """构建 JSON-RPC 错误响应

        Args:
            id: 请求 ID
            code: 错误代码
            message: 错误消息

        Returns:
            JSON 字符串
        """
        error = {"code": code, "message": message}
        # P1 U4 fix: 同 build_response, 直接构造 dict 避免 None 字段泄漏
        return json.dumps(
            {"jsonrpc": "2.0", "id": id, "error": error},
            ensure_ascii=False,
        )

    @classmethod
    def build_notification(cls, method: str, params: Optional[dict[str, Any]] = None) -> str:
        """构建 JSON-RPC 通知

        Args:
            method: 方法名
            params: 参数

        Returns:
            JSON 字符串
        """
        notification = RPCNotification(
            jsonrpc="2.0",
            method=method,
            params=params or {}
        )
        return json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }, ensure_ascii=False)
