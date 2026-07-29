"""Python execution tool with subprocess isolation.

Migration from Hermes Agent code_execution_tool.py
"""

import os
import subprocess
import tempfile
from typing import Optional


def python_exec(code: str, timeout: int = 30, cwd: Optional[str] = None) -> str:
    """Execute Python code in an isolated subprocess.

    Args:
        code: Python code to execute
        timeout: Timeout in seconds (default: 30)
        cwd: Working directory (default: current directory)

    Returns:
        Formatted stdout/stderr output or error message
    """
    work_dir = cwd if cwd else os.getcwd()

    # Write code to a temporary file to avoid shell injection
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", encoding="utf-8", delete=False) as f:
        f.write(code)
        temp_path = f.name

    try:
        # Execute with inherited environment (PYTHONPATH, etc.)
        env = os.environ.copy()

        result = subprocess.run(
            ["python", temp_path],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if result.returncode == 0:
            output = result.stdout if result.stdout else "执行成功（无输出）"
        else:
            output = f"错误:\n{result.stderr}"

        return output

    except subprocess.TimeoutExpired:
        return f"错误: 命令超时 ({timeout}秒)"

    except FileNotFoundError:
        return "错误: Python 解释器未找到"

    except Exception as e:
        return f"错误: {str(e)}"

    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# Schema for OpenAI function calling
SCHEMA = {
    "name": "python_exec",
    "description": "执行 Python 代码脚本，支持超时控制。使用子进程隔离执行。",
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
            "timeout": {"type": "integer", "description": "超时时间（秒）", "default": 30},
            "cwd": {"type": "string", "description": "工作目录（可选）"},
        },
        "required": ["code"],
    },
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="python_exec",
        description=SCHEMA["description"],
        func=lambda **kw: python_exec(
            code=kw.get("code"),
            timeout=kw.get("timeout", 30),
            cwd=kw.get("cwd"),
        ),
        parameters=SCHEMA,
        toolset="terminal",
        emoji="🐍",
        max_result_size_chars=50_000,
    )
