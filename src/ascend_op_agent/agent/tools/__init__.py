"""Tools package - self-registering tool modules.

Each tool module exports a register() function that registers itself
with the global tool registry when imported.
"""

from ascend_op_agent.agent.tool_registry import tool_registry

# Import all tool modules to trigger self-registration
from ascend_op_agent.agent.tools.file_read_tool import register as _file_read
from ascend_op_agent.agent.tools.file_write_tool import register as _file_write
from ascend_op_agent.agent.tools.file_search_tool import register as _file_search
from ascend_op_agent.agent.tools.patch_tool import register as _patch
from ascend_op_agent.agent.tools.shell_tool import register as _shell
from ascend_op_agent.agent.tools.python_exec_tool import register as _python_exec
from ascend_op_agent.agent.tools.git_tool import register as _git
from ascend_op_agent.agent.tools.npu_tool import register as _npu
from ascend_op_agent.agent.tools.skill_manage_tool import register as _skill_manage  # PR-A U4

# Register all tools using direct function + schema
_file_read(tool_registry)
_file_write(tool_registry)
_file_search(tool_registry)
_patch(tool_registry)
_shell(tool_registry)
_python_exec(tool_registry)
_git(tool_registry)
_npu(tool_registry)
_skill_manage(tool_registry)


def list_tools():
    """Return list of all registered tool names."""
    return tool_registry.list_tools()


def get_tool(name: str):
    """Get a tool by name."""
    return tool_registry.get_tool(name)


def to_openai_format():
    """Get all tools in OpenAI function calling format."""
    return tool_registry.to_openai_format()