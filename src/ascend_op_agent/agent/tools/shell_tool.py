"""Shell execution tool with interrupt support.

Migration from Hermes Agent terminal_tool.py (simplified local execution)
"""
import os
import subprocess
import threading
import time
from typing import Optional

# Global interrupt event
_interrupt_event = threading.Event()


def shell_exec(command: str, cwd: Optional[str] = None, timeout: int = 60) -> str:
    """Execute a shell command.

    Args:
        command: Shell command to execute
        cwd: Working directory (default: current directory)
        timeout: Timeout in seconds (default: 60)

    Returns:
        Command output or error message

    Raises:
        TimeoutError: If command times out
    """
    if _interrupt_event.is_set():
        _interrupt_event.clear()
        return "命令被中断"

    # Determine working directory
    work_dir = cwd if cwd else os.getcwd()

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        output = result.stdout if result.returncode == 0 else f"错误: {result.stderr}"
        return output if output else "命令执行成功（无输出）"

    except subprocess.TimeoutExpired:
        return f"错误: 命令超时 ({timeout}秒)"

    except Exception as e:
        return f"错误: {str(e)}"


def interrupt_shell():
    """Interrupt the currently running shell command."""
    _interrupt_event.set()


# Schema for OpenAI function calling
SCHEMA = {
    "name": "shell_exec",
    "description": "执行Shell命令，用于编译、运行脚本、git操作等。",
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell命令"
            },
            "cwd": {
                "type": "string",
                "description": "工作目录（可选）"
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒）",
                "default": 60
            }
        },
        "required": ["command"]
    }
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="shell_exec",
        description=SCHEMA["description"],
        func=lambda **kw: shell_exec(
            command=kw.get("command"),
            cwd=kw.get("cwd"),
            timeout=kw.get("timeout", 60)
        ),
        parameters=SCHEMA,
        toolset="terminal",
        emoji="💻",
        max_result_size_chars=50_000
    )