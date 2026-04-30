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
    ):
        """
        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数
            parameters: OpenAI格式的参数schema
        """
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or self._infer_parameters(func)

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
    ) -> None:
        """注册工具

        Args:
            name: 工具名称
            description: 工具描述
            func: 工具函数
            parameters: OpenAI格式的参数schema
        """
        tool = Tool(name=name, description=description, func=func, parameters=parameters)
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


# 内置工具
def _register_builtin_tools():
    """注册内置工具"""

    @tool(
        name="file_read",
        description="读取文件内容",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
    )
    def file_read(path: str) -> str:
        """读取文件"""
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()

    @tool(
        name="file_write",
        description="写入文件内容",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
    )
    def file_write(path: str, content: str) -> str:
        """写入文件"""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"已写入文件: {path}"

    @tool(
        name="shell_exec",
        description="执行Shell命令",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell命令"},
                "cwd": {"type": "string", "description": "工作目录"},
            },
            "required": ["command"],
        },
    )
    def shell_exec(command: str, cwd: Optional[str] = None) -> str:
        """执行Shell命令"""
        import subprocess
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout or "命令执行成功（无输出）"
        return f"错误: {result.stderr}"


# 注册内置工具
_register_builtin_tools()
