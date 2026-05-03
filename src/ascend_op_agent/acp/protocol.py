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

"""ACP 协议处理器模块

实现 ACP (Agent Client Protocol) 协议解析和消息路由。
扩展现有 JSON-RPC 2.0 协议以支持 ACP 特定方法。
"""

import json
import logging
from typing import Any, Optional

from ascend_op_agent.backend.rpc.protocol import (
    JSONRPCProtocol,
    JSONRPCParseError,
    RPCRequest,
    RPCNotification,
)

logger = logging.getLogger(__name__)


class ACPProtocol:
    """ACP 协议处理器

    扩展 JSON-RPC 2.0 协议以支持 ACP 1.0 方法。
    """

    # ACP 方法定义
    INITIALIZE = "initialize"
    AGENT_RUN = "agent.run"
    AGENT_COMPOSE = "agent.compose"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    NOTIFICATIONS_STATUS = "notifications/status"

    # 支持的方法白名单
    ACP_METHODS = {
        INITIALIZE,
        AGENT_RUN,
        AGENT_COMPOSE,
        TOOLS_LIST,
        TOOLS_CALL,
    }

    def __init__(self):
        self._protocol = JSONRPCProtocol()

    def parse_message(self, raw: str) -> tuple[Optional[RPCRequest], Optional[RPCNotification]]:
        """解析 JSON-RPC 消息

        Args:
            raw: JSON 字符串

        Returns:
            (RPCRequest, None) 或 (None, RPCNotification)

        Raises:
            JSONRPCParseError: 解析失败
        """
        return self._protocol.parse_request(raw)

    def validate_method(self, method: str) -> bool:
        """验证方法是否在白名单中

        Args:
            method: 方法名

        Returns:
            是否有效
        """
        return method in self.ACP_METHODS

    def validate_params(self, method: str, params: Optional[dict[str, Any]]) -> bool:
        """验证参数是否符合 schema

        Args:
            method: 方法名
            params: 参数字典

        Returns:
            是否有效
        """
        if params is None:
            return True

        if method == self.INITIALIZE:
            return isinstance(params, dict)
        elif method == self.AGENT_RUN:
            return isinstance(params, dict) and "user_input" in params
        elif method == self.AGENT_COMPOSE:
            return isinstance(params, dict) and "messages" in params
        elif method == self.TOOLS_LIST:
            return True
        elif method == self.TOOLS_CALL:
            return isinstance(params, dict) and "name" in params and "args" in params
        return True

    def build_initialize_response(self, capabilities: dict[str, Any]) -> str:
        """构建 initialize 响应

        Args:
            capabilities: 服务器能力

        Returns:
            JSON 字符串
        """
        result = {
            "protocolVersion": "1.0",
            "capabilities": capabilities,
            "serverInfo": {
                "name": "ascend-op-agent-acp",
                "version": "1.0.0",
            }
        }
        return self._protocol.build_response(1, result)

    def build_error_response(self, id: Any, code: int, message: str) -> str:
        """构建错误响应

        Args:
            id: 请求 ID
            code: 错误代码
            message: 错误消息

        Returns:
            JSON 字符串
        """
        return self._protocol.build_error(id, code, message)

    def build_success_response(self, id: Any, result: Any) -> str:
        """构建成功响应

        Args:
            id: 请求 ID
            result: 响应结果

        Returns:
            JSON 字符串
        """
        return self._protocol.build_response(id, result)

    def build_notification(self, method: str, params: Optional[dict[str, Any]] = None) -> str:
        """构建通知消息

        Args:
            method: 方法名
            params: 参数

        Returns:
            JSON 字符串
        """
        return self._protocol.build_notification(method, params)

    def get_method_info(self, method: str) -> dict[str, Any]:
        """获取方法信息

        Args:
            method: 方法名

        Returns:
            方法描述字典
        """
        method_info = {
            self.INITIALIZE: {
                "description": "初始化会话，传递客户端能力",
                "direction": "Editor→Agent",
                "params": ["clientInfo", "capabilities"],
            },
            self.AGENT_RUN: {
                "description": "运行 Agent 对话",
                "direction": "Editor→Agent",
                "params": ["user_input"],
            },
            self.AGENT_COMPOSE: {
                "description": "发送组合消息",
                "direction": "Editor→Agent",
                "params": ["messages"],
            },
            self.TOOLS_LIST: {
                "description": "列出可用工具",
                "direction": "Editor→Agent",
                "params": [],
            },
            self.TOOLS_CALL: {
                "description": "调用工具",
                "direction": "Editor→Agent",
                "params": ["name", "args"],
            },
            self.NOTIFICATIONS_STATUS: {
                "description": "推送状态更新",
                "direction": "Agent→Editor",
                "params": ["status", "message"],
            },
        }
        return method_info.get(method, {})