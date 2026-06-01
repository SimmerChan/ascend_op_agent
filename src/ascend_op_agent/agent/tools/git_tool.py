"""Git operations tool with subprocess git commands.

Provides git_log, git_diff, git_status, git_branch functions.
"""
import os
import subprocess
from typing import Optional, List


def git_log(
    path: str = ".",
    max_count: int = 50,
    format: str = "%h %s %an %ad"
) -> str:
    """Get git commit log.

    Args:
        path: Repository path (default: current directory)
        max_count: Maximum number of commits to return
        format: Git log format string

    Returns:
        Formatted commit log or error message
    """
    work_dir = os.path.abspath(path)

    if not os.path.exists(os.path.join(work_dir, '.git')):
        return f"错误: {path} 不是 Git 仓库"

    try:
        result = subprocess.run(
            ['git', 'log', f'-{max_count}', f'--format={format}'],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout if result.stdout else "无提交记录"
        else:
            return f"错误: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: git log 超时"

    except FileNotFoundError:
        return "错误: Git 未安装"

    except Exception as e:
        return f"错误: {str(e)}"


def git_diff(
    path: str = ".",
    ref1: Optional[str] = None,
    ref2: Optional[str] = None,
    staged: bool = False
) -> str:
    """Get git diff between refs or working tree.

    Args:
        path: Repository path
        ref1: First ref (or None for working tree)
        ref2: Second ref (or None for working tree or staged)
        staged: If True, show staged changes

    Returns:
        Diff output or error message
    """
    work_dir = os.path.abspath(path)

    if not os.path.exists(os.path.join(work_dir, '.git')):
        return f"错误: {path} 不是 Git 仓库"

    try:
        args = ['git', 'diff', '--no-color']

        if staged:
            args.append('--staged')

        if ref1 and ref2:
            args.append(f'{ref1}..{ref2}')
        elif ref1:
            args.append(ref1)

        result = subprocess.run(
            args,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout if result.stdout else "无差异"
        else:
            return f"错误: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: git diff 超时"

    except FileNotFoundError:
        return "错误: Git 未安装"

    except Exception as e:
        return f"错误: {str(e)}"


def git_status(path: str = ".") -> str:
    """Get git status in porcelain format.

    Args:
        path: Repository path

    Returns:
        Status output or error message
    """
    work_dir = os.path.abspath(path)

    if not os.path.exists(os.path.join(work_dir, '.git')):
        return f"错误: {path} 不是 Git 仓库"

    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            output = result.stdout.strip()
            if not output:
                return "工作区干净"
            return output
        else:
            return f"错误: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: git status 超时"

    except FileNotFoundError:
        return "错误: Git 未安装"

    except Exception as e:
        return f"错误: {str(e)}"


def git_branch(path: str = ".", list_branches: bool = True) -> str:
    """List git branches or get current branch.

    Args:
        path: Repository path
        list_branches: If True, list all branches

    Returns:
        Branch information or error message
    """
    work_dir = os.path.abspath(path)

    if not os.path.exists(os.path.join(work_dir, '.git')):
        return f"错误: {path} 不是 Git 仓库"

    try:
        if list_branches:
            result = subprocess.run(
                ['git', 'branch', '-v'],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            result = subprocess.run(
                ['git', 'branch', '--show-current'],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )

        if result.returncode == 0:
            return result.stdout.strip() if result.stdout else "无分支"
        else:
            return f"错误: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: git branch 超时"

    except FileNotFoundError:
        return "错误: Git 未安装"

    except Exception as e:
        return f"错误: {str(e)}"


# Schema for OpenAI function calling - git_log
GIT_LOG_SCHEMA = {
    "name": "git_log",
    "description": "查询 Git 提交历史记录。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "仓库路径",
                "default": "."
            },
            "max_count": {
                "type": "integer",
                "description": "最大提交数",
                "default": 50
            }
        },
        "required": []
    }
}

# Schema for git_diff
GIT_DIFF_SCHEMA = {
    "name": "git_diff",
    "description": "查看 Git 差异，支持比较提交、分支或工作区。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "仓库路径",
                "default": "."
            },
            "ref1": {
                "type": "string",
                "description": "第一个引用（可选）"
            },
            "ref2": {
                "type": "string",
                "description": "第二个引用（可选）"
            },
            "staged": {
                "type": "boolean",
                "description": "是否显示暂存区差异",
                "default": False
            }
        },
        "required": []
    }
}

# Schema for git_status
GIT_STATUS_SCHEMA = {
    "name": "git_status",
    "description": "查看 Git 仓库状态（工作区、暂存区）。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "仓库路径",
                "default": "."
            }
        },
        "required": []
    }
}

# Schema for git_branch
GIT_BRANCH_SCHEMA = {
    "name": "git_branch",
    "description": "列出或查看 Git 分支。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "仓库路径",
                "default": "."
            },
            "list_branches": {
                "type": "boolean",
                "description": "列出所有分支",
                "default": True
            }
        },
        "required": []
    }
}


def register(registry):
    """Register all git tools with the registry."""
    registry.register(
        name="git_log",
        description=GIT_LOG_SCHEMA["description"],
        func=lambda **kw: git_log(
            path=kw.get("path", "."),
            max_count=kw.get("max_count", 50),
        ),
        parameters=GIT_LOG_SCHEMA,
        toolset="vcs",
        emoji="📜",
        max_result_size_chars=50_000
    )

    registry.register(
        name="git_diff",
        description=GIT_DIFF_SCHEMA["description"],
        func=lambda **kw: git_diff(
            path=kw.get("path", "."),
            ref1=kw.get("ref1"),
            ref2=kw.get("ref2"),
            staged=kw.get("staged", False),
        ),
        parameters=GIT_DIFF_SCHEMA,
        toolset="vcs",
        emoji="📊",
        max_result_size_chars=50_000
    )

    registry.register(
        name="git_status",
        description=GIT_STATUS_SCHEMA["description"],
        func=lambda **kw: git_status(
            path=kw.get("path", "."),
        ),
        parameters=GIT_STATUS_SCHEMA,
        toolset="vcs",
        emoji="📋",
        max_result_size_chars=50_000
    )

    registry.register(
        name="git_branch",
        description=GIT_BRANCH_SCHEMA["description"],
        func=lambda **kw: git_branch(
            path=kw.get("path", "."),
            list_branches=kw.get("list_branches", True),
        ),
        parameters=GIT_BRANCH_SCHEMA,
        toolset="vcs",
        emoji="🌿",
        max_result_size_chars=50_000
    )
