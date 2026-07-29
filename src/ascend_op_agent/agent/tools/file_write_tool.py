"""File write tool with atomic writes and backup.

Migration from Hermes Agent file_tools.py
"""

import os
import shutil
from pathlib import Path
from typing import Optional

BACKUP_DIR = ".backup"


def file_write(path: str, content: str, create_dirs: bool = True, backup: bool = True) -> str:
    """Write content to a file.

    Args:
        path: File path to write
        content: Content to write
        create_dirs: Create parent directories if they don't exist
        backup: Create backup of existing file

    Returns:
        Success message

    Raises:
        ValueError: If path is invalid or write fails
    """
    abs_path = os.path.abspath(path)

    # Security: prevent writing to sensitive paths
    blocked = ["/dev/", "/proc/", "/sys/"]
    for blocked_path in blocked:
        if abs_path.startswith(blocked_path):
            raise ValueError(f"禁止写入: {path}")

    # Create parent directories
    if create_dirs:
        parent = os.path.dirname(abs_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

    # Backup existing file
    if backup and os.path.exists(abs_path):
        backup_dir = os.path.join(os.path.dirname(abs_path), BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)

        filename = os.path.basename(abs_path)
        backup_name = f"{filename}.{int(os.path.getmtime(abs_path))}.bak"
        backup_path = os.path.join(backup_dir, backup_name)

        try:
            shutil.copy2(abs_path, backup_path)
        except Exception:
            pass  # Backup failed, continue anyway

    # Atomic write: write to temp file then rename
    temp_path = abs_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(temp_path, abs_path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise ValueError(f"写入失败: {e}")

    return f"已写入文件: {path} ({len(content)} chars)"


# Schema for OpenAI function calling
SCHEMA = {
    "name": "file_write",
    "description": "写入文件内容，自动创建目录。现有文件会先备份。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "文件内容"},
            "create_dirs": {"type": "boolean", "description": "自动创建父目录", "default": True},
            "backup": {"type": "boolean", "description": "备份现有文件", "default": True},
        },
        "required": ["path", "content"],
    },
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="file_write",
        description=SCHEMA["description"],
        func=lambda **kw: file_write(
            path=kw.get("path"),
            content=kw.get("content"),
            create_dirs=kw.get("create_dirs", True),
            backup=kw.get("backup", True),
        ),
        parameters=SCHEMA,
        toolset="file",
        emoji="📝",
        max_result_size_chars=50_000,
    )
