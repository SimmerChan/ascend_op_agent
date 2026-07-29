"""File read tool with safety checks and caching.

Migration from Hermes Agent file_tools.py
"""

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Optional

# Cache for deduplication: path -> (mtime, content_hash, content)
_read_cache: dict[str, tuple[float, str, str]] = {}
# Consecutive identical read counter: path -> count
_read_counter: dict[str, int] = {}
# Blocked paths pattern
BLOCKED_PATTERNS = [
    r"^/dev/",
    r"^/proc/",
    r"^/sys/",
    r"^/tmp/.*\.tmp$",
]

MAX_FILE_READ_CHARS = 100_000
LOOP_THRESHOLD = 4


def _is_blocked_path(path: str) -> bool:
    """Check if path is blocked for security reasons."""
    for pattern in BLOCKED_PATTERNS:
        if re.match(pattern, path):
            return True
    return False


def _compute_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def file_read(path: str, offset: int = 1, limit: int = 500) -> str:
    """Read a file with safety checks.

    Args:
        path: File path to read
        offset: Line number to start from (1-based)
        limit: Maximum number of lines to read

    Returns:
        File content as string

    Raises:
        ValueError: If path is blocked, file is binary, or read is blocked
    """
    abs_path = os.path.abspath(path)

    # Security check
    if _is_blocked_path(abs_path):
        raise ValueError(f"访问被拒绝: {path}")

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {path}")

    if not os.path.isfile(abs_path):
        raise ValueError(f"不是文件: {path}")

    # Binary file check
    binary_extensions = {
        ".exe",
        ".bin",
        ".so",
        ".dylib",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".class",
        ".pyc",
        ".pyo",
    }
    if any(abs_path.endswith(ext) for ext in binary_extensions):
        raise ValueError(f"二进制文件不支持: {path}")

    try:
        mtime = os.path.getmtime(abs_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ValueError(f"无法解码为文本: {path}")

    # Size check
    if len(content) > MAX_FILE_READ_CHARS:
        raise ValueError(f"文件过大 ({len(content)} chars > {MAX_FILE_READ_CHARS})")

    # Deduplication check
    cache_key = abs_path
    content_hash = _compute_hash(content)

    if cache_key in _read_cache:
        cached_mtime, cached_hash, cached_content = _read_cache[cache_key]
        if cached_mtime == mtime and cached_hash == content_hash:
            # Same file, return cached
            counter = _read_counter.get(cache_key, 0) + 1
            _read_counter[cache_key] = counter
            if counter >= LOOP_THRESHOLD:
                return f"[文件已相同，跳过重复读取: {path}]\n{cached_content[:200]}..."
            return cached_content

    # Update cache
    _read_cache[cache_key] = (mtime, content_hash, content)
    _read_counter[cache_key] = 0

    # Line pagination
    lines = content.split("\n")
    start = max(0, offset - 1)
    end = start + limit
    paginated = "\n".join(lines[start:end])

    if end < len(lines):
        return f"[lines {offset}-{end}/{len(lines)}]\n{paginated}"
    return paginated


# Schema for OpenAI function calling
SCHEMA = {
    "name": "file_read",
    "description": "读取文件内容，支持分页。用于查看代码、配置、文档等文件。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "offset": {"type": "integer", "description": "起始行号 (1-based)", "default": 1},
            "limit": {"type": "integer", "description": "最大行数", "default": 500},
        },
        "required": ["path"],
    },
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="file_read",
        description=SCHEMA["description"],
        func=lambda **kw: file_read(
            path=kw.get("path"), offset=kw.get("offset", 1), limit=kw.get("limit", 500)
        ),
        parameters=SCHEMA,
        toolset="file",
        emoji="📖",
        max_result_size_chars=100_000,
    )
