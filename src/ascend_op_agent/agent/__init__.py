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

"""Ascend Op Agent 核心引擎

包含:
- AIAgent: 会话管理和迭代控制
- PromptBuilder: 7层Prompt组装
- ToolRegistry: 自注册工具系统
- ContextEngine: 上下文压缩和检索
- MemoryStore: 持久化记忆
"""

from ascend_op_agent.agent.core import AIAgent
from ascend_op_agent.agent.context import ContextEngine
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.agent.tool_registry import ToolRegistry, Tool, tool_registry

__all__ = [
    "AIAgent",
    "ContextEngine",
    "MemoryStore",
    "PromptBuilder",
    "Tool",
    "ToolRegistry",
    "tool_registry",
]
