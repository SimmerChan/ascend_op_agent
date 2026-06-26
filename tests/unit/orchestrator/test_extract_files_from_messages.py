"""e2e fallback 提取器单测:从 LLM assistant 文本里抽代码块。

LLM tool calling 不可靠时(2026-06-25 第二次 e2e 跑,LLM 0 个 file_write)用这个
fallback 从 assistant 文本 markdown 代码块抽文件。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 加 scripts/ 到 import path(不污染 src/)
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "scripts"))

from e2e_real_op import _extract_files_from_messages  # noqa: E402


def test_extracts_cpp_block_with_double_slash_path() -> None:
    messages = [
        {"role": "user", "content": "design an add op"},
        {
            "role": "assistant",
            "content": (
                "Here's the code:\n\n"
                "```cpp\n"
                "// /tmp/op/op_kernel.cpp\n"
                "#include <kernel>\n"
                "void Init() {}\n"
                "```\n"
            ),
        },
    ]
    result = _extract_files_from_messages(messages)
    assert len(result) == 1
    assert result[0]["path"] == "/tmp/op/op_kernel.cpp"
    assert "Init()" in result[0]["content"]
    # 路径注释行不应出现在 content 里
    assert "// /tmp/op/op_kernel.cpp" not in result[0]["content"]


def test_extracts_bash_block_with_hash_file_path() -> None:
    messages = [
        {"role": "assistant", "content": (
            "```bash\n"
            "# File: /tmp/op/build.sh\n"
            "#!/bin/bash\n"
            "set -e\n"
            "cmake -B build\n"
            "```\n"
        )},
    ]
    result = _extract_files_from_messages(messages)
    assert len(result) == 1
    assert result[0]["path"] == "/tmp/op/build.sh"
    assert "cmake -B build" in result[0]["content"]


def test_extracts_multiple_files_in_one_response() -> None:
    messages = [
        {"role": "assistant", "content": (
            "Two files:\n"
            "```cpp\n"
            "// /tmp/op/a.cpp\n"
            "int a() { return 1; }\n"
            "```\n"
            "and\n"
            "```cpp\n"
            "// /tmp/op/b.h\n"
            "int b();\n"
            "```\n"
        )},
    ]
    result = _extract_files_from_messages(messages)
    paths = [f["path"] for f in result]
    assert "/tmp/op/a.cpp" in paths
    assert "/tmp/op/b.h" in paths


def test_skips_blocks_without_path() -> None:
    messages = [
        {"role": "assistant", "content": (
            "```cpp\n"
            "int no_path() { return 0; }\n"  # 没路径注释
            "```\n"
        )},
    ]
    result = _extract_files_from_messages(messages)
    assert result == []


def test_dedup_same_path() -> None:
    messages = [
        {"role": "assistant", "content": (
            "```cpp\n// /tmp/op/a.cpp\nint a() { return 1; }\n```\n"
            "```cpp\n// /tmp/op/a.cpp\nint a() { return 2; }\n```\n"
        )},
    ]
    result = _extract_files_from_messages(messages)
    assert len(result) == 1
    assert "return 1;" in result[0]["content"]


def test_takes_last_assistant_message() -> None:
    """多个 assistant 消息时,取最新的(倒序遍历)。"""
    messages = [
        {"role": "assistant", "content": "```cpp\n// /tmp/op/old.cpp\nint old() { return 0; }\n```"},
        {"role": "assistant", "content": "```cpp\n// /tmp/op/new.cpp\nint new() { return 1; }\n```"},
    ]
    result = _extract_files_from_messages(messages)
    paths = [f["path"] for f in result]
    assert "/tmp/op/new.cpp" in paths
    assert "/tmp/op/old.cpp" in paths


def test_empty_messages_returns_empty() -> None:
    assert _extract_files_from_messages([]) == []


def test_tool_call_only_message_is_skipped() -> None:
    """纯 tool_call 消息(没代码块)不污染提取。"""
    messages = [
        {"role": "assistant", "content": "tool_call(shell_exec)"},
        {"role": "tool", "content": "some output"},
        {"role": "assistant", "content": "I called a tool"},
    ]
    result = _extract_files_from_messages(messages)
    assert result == []
