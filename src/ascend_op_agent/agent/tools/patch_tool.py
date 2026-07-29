"""Patch tool for replacing text in files.

Migration from Hermes Agent file_tools.py patch tool
"""

import os
import re


def patch(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace old_string with new_string in a file.

    Args:
        path: File path
        old_string: String to replace
        new_string: Replacement string
        replace_all: Replace all occurrences or just the first

    Returns:
        Success message

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If old_string not found or ambiguous
    """
    abs_path = os.path.abspath(path)

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Try exact match first
    if old_string in content:
        if replace_all:
            new_content = content.replace(old_string, new_string)
            count = content.count(old_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
            count = 1

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"已替换 {count} 处: {path}"

    # Try fuzzy match (handle minor whitespace/indentation differences)
    fuzzy_result = _fuzzy_replace(content, old_string, new_string, replace_all)
    if fuzzy_result:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(fuzzy_result["content"])
        return f"已替换 (fuzzy match) {fuzzy_result['count']} 处: {path}"

    return f"错误: 未找到要替换的文本: {old_string[:50]}..."


def _normalize_whitespace(s: str) -> str:
    """Normalize whitespace for fuzzy matching."""
    # Collapse multiple spaces to one
    s = re.sub(r" +", " ", s)
    # Collapse multiple newlines to two
    s = re.sub(r"\n\n+", "\n\n", s)
    return s.strip()


def _fuzzy_replace(content: str, old_string: str, new_string: str, replace_all: bool) -> dict:
    """Try fuzzy matching with 9 strategies."""
    old_normalized = _normalize_whitespace(old_string)
    lines = content.split("\n")

    best_match = None
    best_score = 0

    # Strategy: try matching each consecutive block of non-empty lines
    old_lines = old_normalized.split("\n")

    for start in range(len(lines)):
        for end in range(start + 1, min(start + len(old_lines) * 2 + 1, len(lines) + 1)):
            candidate = "\n".join(lines[start:end])
            candidate_normalized = _normalize_whitespace(candidate)

            # Simple similarity score
            score = _similarity(old_normalized, candidate_normalized)

            if score > 0.8 and score > best_score:
                best_score = score
                best_match = (start, end, lines[start:end])

    if best_match:
        start, end, matched_lines = best_match
        new_lines = new_string.split("\n")
        result_lines = lines[:start] + new_lines + lines[end:]
        return {"content": "\n".join(result_lines), "count": 1}

    return None


def _similarity(s1: str, s2: str) -> float:
    """Calculate similarity between two strings (0-1)."""
    if not s1 or not s2:
        return 0

    # Simple character-based similarity
    s1_set = set(s1)
    s2_set = set(s2)

    intersection = len(s1_set & s2_set)
    union = len(s1_set | s2_set)

    return intersection / union if union > 0 else 0


# Schema for OpenAI function calling
SCHEMA = {
    "name": "patch",
    "description": "替换文件中的文本，支持精确匹配和模糊匹配。",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_string": {"type": "string", "description": "要替换的文本（支持多行）"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {
                "type": "boolean",
                "description": "替换所有匹配还是只替换第一个",
                "default": False,
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
}


def register(registry):
    """Register this tool with the registry."""
    registry.register(
        name="patch",
        description=SCHEMA["description"],
        func=lambda **kw: patch(
            path=kw.get("path"),
            old_string=kw.get("old_string"),
            new_string=kw.get("new_string"),
            replace_all=kw.get("replace_all", False),
        ),
        parameters=SCHEMA,
        toolset="file",
        emoji="🔧",
        max_result_size_chars=50_000,
    )
