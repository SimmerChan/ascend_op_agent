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

"""MCP 模块

MCP (Model Context Protocol) 服务器集成，支持 stdio、HTTP 和 streamable-http 三种传输模式。
"""

from ascend_op_agent.mcp.client import MCPClient, MCPConnectionError
from ascend_op_agent.mcp.lifecycle import MCPLifecycleManager
from ascend_op_agent.mcp.oauth import MCPOAuthManager
from ascend_op_agent.mcp.server_config import MCPServerConfig, TransportType

__all__ = [
    "MCPClient",
    "MCPConnectionError",
    "MCPLifecycleManager",
    "MCPOAuthManager",
    "MCPServerConfig",
    "TransportType",
]
