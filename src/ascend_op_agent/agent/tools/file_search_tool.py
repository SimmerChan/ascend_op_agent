"""File search tool using grep-like pattern matching.

Migration from Hermes Agent file_tools.py search_files
"""

import os
import re
from pathlib import Path
from typing import Optional

DEFAULT_LIMIT = 50


def file_search(
    pattern: str,
    path: str = ".",
    target: str = "content",
    file_glob: Optional[str] = None,
    output_mode: str = "content",
    context: int = 0,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> str:
    """Search for pattern in files.

    Args:
        pattern: Search pattern (regex for content, glob for files)
        path: Directory to search in
        target: "content" or "files"
        file_glob: Optional glob pattern to filter files (e.g., "*.py")
        output_mode: "content", "files_only", or "count"
        context: Number of lines before/after to include
        limit: Maximum results
        offset: Skip first N results

    Returns:
        Search results as formatted string
    """
    if not pattern:
        return (
            "错误: file_search 缺少必填参数 pattern (搜索模式正则表达式)。"
            "请提供 pattern 参数后重试,例如 file_search(pattern='build.sh')。"
        )
    abs_path = os.path.abspath(path)

    if not os.path.exists(abs_path):
        return f"路径不存在: {path}"

    if not os.path.isdir(abs_path):
        return f"不是目录: {path}"

    results = []

    if target == "files":
        # Find files by name (glob)
        try:
            pattern_re = re.compile(pattern) if pattern else None
        except re.error:
            return f"无效的正则表达式: {pattern}"

        for root, dirs, files in os.walk(abs_path):
            # Skip hidden and common ignore directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv"}
            ]

            for filename in files:
                # Glob filter
                if file_glob:
                    import fnmatch

                    if not fnmatch.fnmatch(filename, file_glob):
                        continue

                # Name match
                if pattern_re:
                    if pattern_re.search(filename):
                        full_path = os.path.join(root, filename)
                        rel_path = os.path.relpath(full_path, abs_path)
                        results.append(rel_path)
                else:
                    full_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(full_path, abs_path)
                    results.append(rel_path)

    else:
        # Search content
        try:
            pattern_re = re.compile(pattern)
        except re.error:
            return f"无效的正则表达式: {pattern}"

        for root, dirs, files in os.walk(abs_path):
            # Skip hidden and common ignore directories
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in {"node_modules", "__pycache__", "venv"}
            ]

            for filename in files:
                # Glob filter
                if file_glob:
                    import fnmatch

                    if not fnmatch.fnmatch(filename, file_glob):
                        continue

                # Skip binary files
                if any(filename.endswith(ext) for ext in {".pyc", ".pyo", ".bin", ".so", ".dylib"}):
                    continue

                full_path = os.path.join(root, filename)

                try:
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                except Exception:
                    continue

                for i, line in enumerate(lines):
                    if pattern_re.search(line):
                        rel_path = os.path.relpath(full_path, abs_path)
                        line_num = i + 1

                        if output_mode == "count":
                            results.append(f"{rel_path}:{line_num}")
                        else:
                            # Include context lines
                            start = max(0, i - context)
                            end = min(len(lines), i + context + 1)
                            context_lines = lines[start:end]

                            header = f"{rel_path}:{line_num}: {line.rstrip()}"
                            results.append(header)

                            if context > 0:
                                for j, ctx_line in enumerate(context_lines[start:], start):
                                    if j != i:
                                        results.append(f"  {j + 1}: {ctx_line.rstrip()}")

    # Apply offset and limit
    total = len(results)
    results = results[offset : offset + limit]

    # Format output
    if output_mode == "count":
        return f"找到 {total} 个匹配"

    output = f"找到 {total} 个匹配:\n"
    output += "\n".join(results[:limit])

    if total > limit:
        output += f"\n... 还有 {total - limit} 个结果"

    return output


# Schema for OpenAI function calling
SCHEMA = {
    "name": "file_search",
    "description": "搜索文件内容或文件名，支持正则表达式和 glob 模式。",
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "搜索模式（正则表达式）"},
            "path": {"type": "string", "description": "搜索目录", "default": "."},
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "搜索内容还是文件名",
                "default": "content",
            },
            "file_glob": {"type": "string", "description": "文件过滤 glob 模式 (e.g., *.py)"},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": "输出格式",
                "default": "content",
            },
            "context": {"type": "integer", "description": "上下文行数", "default": 0},
            "limit": {"type": "integer", "description": "最大结果数", "default": 50},
        },
        "required": ["pattern"],
    },
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="file_search",
        description=SCHEMA["description"],
        func=lambda **kw: file_search(
            pattern=kw.get("pattern"),
            path=kw.get("path", "."),
            target=kw.get("target", "content"),
            file_glob=kw.get("file_glob"),
            output_mode=kw.get("output_mode", "content"),
            context=kw.get("context", 0),
            limit=kw.get("limit", DEFAULT_LIMIT),
            offset=kw.get("offset", 0),
        ),
        parameters=SCHEMA,
        toolset="file",
        emoji="🔍",
        max_result_size_chars=100_000,
    )
