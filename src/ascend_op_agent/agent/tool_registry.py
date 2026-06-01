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

"""ToolRegistry - 自注册工具系统

参考Hermes Agent的ToolRegistry自注册机制。
"""

import inspect
from typing import Any, Callable, Optional


class Tool:
    """工具基类"""

    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        parameters: Optional[dict] = None,
        toolset: str = "default",
        emoji: str = "",
        max_result_size_chars: Optional[int] = None,
    ):
        """
        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数
            parameters: OpenAI格式的参数schema
            toolset: 工具集分组
            emoji: 表情图标
            max_result_size_chars: 结果最大字符数
        """
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or self._infer_parameters(func)
        self.toolset = toolset
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars

    def _infer_parameters(self, func: Callable) -> dict:
        """从函数签名推断参数schema"""
        sig = inspect.signature(func)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ('self', 'cls'):
                continue

            param_type = "string"
            if param.annotation in (int,):
                param_type = "integer"
            elif param.annotation in (float,):
                param_type = "number"
            elif param.annotation in (bool,):
                param_type = "boolean"
            elif param.annotation in (list,):
                param_type = "array"
            elif param.annotation in (dict,):
                param_type = "object"

            properties[param_name] = {
                "type": param_type,
                "description": f"Parameter {param_name}",
            }

            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def execute(self, **kwargs) -> Any:
        """执行工具"""
        return self.func(**kwargs)

    def to_openai_format(self) -> dict:
        """转换为OpenAI工具格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册表

    支持自注册机制，可以通过@tool装饰器或直接调用register()注册工具。
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        func: Callable[..., Any],
        parameters: Optional[dict] = None,
        toolset: str = "default",
        emoji: str = "",
        max_result_size_chars: Optional[int] = None,
        schema: Optional[dict] = None,
    ) -> None:
        """注册工具

        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数
            parameters: OpenAI格式的参数schema
            toolset: 工具集分组
            emoji: 表情图标
            max_result_size_chars: 结果最大字符数
            schema: 参数schema（parameters 的别名）
        """
        # schema 是 parameters 的别名
        if schema is not None and parameters is None:
            parameters = schema
        tool = Tool(
            name=name,
            description=description,
            func=func,
            parameters=parameters,
            toolset=toolset,
            emoji=emoji,
            max_result_size_chars=max_result_size_chars,
        )
        self._tools[name] = tool

    def get_tool(self, name: str) -> Optional[Tool]:
        """获取工具"""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """列出所有已注册工具"""
        return list(self._tools.keys())

    def get_all_tools(self) -> list[Tool]:
        """获取所有工具"""
        return list(self._tools.values())

    def to_openai_format(self) -> list[dict]:
        """转换为OpenAI工具列表格式"""
        return [tool.to_openai_format() for tool in self._tools.values()]

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """调用工具并返回结构化结果

        Args:
            name: 工具名称
            args: 工具参数

        Returns:
            结构化字典: {"success": true, "result": <value>}
                     或 {"success": false, "error": <message>}
        """
        tool = self.get_tool(name)
        if not tool:
            return {"success": False, "error": f"错误: 未知工具: {name}"}
        try:
            result = tool.execute(**args)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": f"错误: 工具执行失败: {e}"}


# 全局工具注册表实例
tool_registry = ToolRegistry()


def tool(
    name: str,
    description: str,
    parameters: Optional[dict] = None,
) -> Callable:
    """工具装饰器

    用法:
        @tool(name="my_tool", description="这是一个测试工具")
        def my_tool(arg1: str, arg2: int) -> str:
            return f"{arg1} {arg2}"
    """
    def decorator(func: Callable) -> Callable:
        tool_registry.register(
            name=name,
            description=description,
            func=func,
            parameters=parameters,
        )
        return func
    return decorator


# 内置工具 (已迁移到 tools/ 包)
# _register_builtin_tools() 已移除，工具通过 tools/__init__.py 注册
