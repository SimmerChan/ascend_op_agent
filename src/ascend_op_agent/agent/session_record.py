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

"""Session Record - 完整对话记录数据结构

参考 Claude Code 的 sessionStorage.ts 设计，采用 JSONL Append-only 格式存储。
支持用户输入、LLM 输入输出、工具调用的完整记录。
"""

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# Token 估算：按字符数 / 4（简单估算，对 CJK 语言不准确，V2 应使用语言感知的估算）
TOKEN_ESTIMATE_MULTIPLIER = 4

# 最大单条记录字节数（100MB），超过则分块
MAX_CHUNK_BYTES = 100 * 1024 * 1024


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量

    注意：简单的 char/4 估算对 CJK 语言不准确，V2 应使用 language-aware 估算。
    """
    return len(text) // TOKEN_ESTIMATE_MULTIPLIER


@dataclass
class Entry:
    """对话记录条目基类"""
    type: str  # user, assistant, system, tool
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    model: Optional[str] = None
    provider: Optional[str] = None
    turn_id: int = 0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None  # 父节点 ID，用于构建树形关系

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """序列化为 JSON 行"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class UserEntry(Entry):
    """用户输入条目"""
    type: str = "user"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "turn_id": self.turn_id,
            "id": self.id,
            "parent_id": self.parent_id,
            "content": self.content,
            "token_count": estimate_tokens(self.content),
        }


@dataclass
class SystemEntry(Entry):
    """系统提示条目（记录发送给 LLM 的完整 system prompt）"""
    type: str = "system"
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "turn_id": self.turn_id,
            "id": self.id,
            "parent_id": self.parent_id,
            "content": self.content,
            "token_count": estimate_tokens(self.content),
        }


@dataclass
class LLMEntry(Entry):
    """LLM 输入输出条目"""
    type: str = "assistant"
    input_messages: list[dict[str, str]] = field(default_factory=list)
    output_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "turn_id": self.turn_id,
            "id": self.id,
            "parent_id": self.parent_id,
            "input_messages": self.input_messages,
            "output_content": self.output_content,
            "token_count": estimate_tokens(self.output_content),
            "tool_calls": self.tool_calls,
        }


@dataclass
class ToolEntry(Entry):
    """工具调用条目"""
    type: str = "tool"
    tool_name: str = ""
    tool_call_id: str = ""  # Native Function Calling 的 tool_call_id
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "turn_id": self.turn_id,
            "id": self.id,
            "parent_id": self.parent_id,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "arguments": self.arguments,
            "result": self.result,
            "success": self.success,
            "error": self.error,
            "token_count": estimate_tokens(self.result),
        }


def entry_from_dict(data: dict[str, Any]) -> Entry:
    """从字典反序列化 Entry"""
    entry_type = data.get("type", "")

    if entry_type == "user":
        return UserEntry(
            type=data.get("type", "user"),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            model=data.get("model"),
            provider=data.get("provider"),
            turn_id=data.get("turn_id", 0),
            id=data.get("id", str(uuid.uuid4())),
            parent_id=data.get("parent_id"),
            content=data.get("content", ""),
        )
    elif entry_type == "system":
        return SystemEntry(
            type=data.get("type", "system"),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            model=data.get("model"),
            provider=data.get("provider"),
            turn_id=data.get("turn_id", 0),
            id=data.get("id", str(uuid.uuid4())),
            parent_id=data.get("parent_id"),
            content=data.get("content", ""),
        )
    elif entry_type == "assistant":
        return LLMEntry(
            type=data.get("type", "assistant"),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            model=data.get("model"),
            provider=data.get("provider"),
            turn_id=data.get("turn_id", 0),
            id=data.get("id", str(uuid.uuid4())),
            parent_id=data.get("parent_id"),
            input_messages=data.get("input_messages", []),
            output_content=data.get("output_content", ""),
            tool_calls=data.get("tool_calls", []),
        )
    elif entry_type == "tool":
        return ToolEntry(
            type=data.get("type", "tool"),
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            model=data.get("model"),
            provider=data.get("provider"),
            turn_id=data.get("turn_id", 0),
            id=data.get("id", str(uuid.uuid4())),
            parent_id=data.get("parent_id"),
            tool_name=data.get("tool_name", ""),
            tool_call_id=data.get("tool_call_id", ""),
            arguments=data.get("arguments", {}),
            result=data.get("result", ""),
            success=data.get("success", True),
            error=data.get("error"),
        )
    else:
        # 未知类型，返回通用 Entry
        return Entry(
            type=entry_type,
            timestamp=data.get("timestamp", time.time()),
            session_id=data.get("session_id", ""),
            model=data.get("model"),
            provider=data.get("provider"),
            turn_id=data.get("turn_id", 0),
            id=data.get("id", str(uuid.uuid4())),
        )


def entry_from_json(line: str) -> Entry:
    """从 JSON 行反序列化 Entry"""
    data = json.loads(line)
    return entry_from_dict(data)


def chunk_content(content: str) -> list[str]:
    """将内容分块，避免单条记录过大

    当内容超过 MAX_CHUNK_BYTES 时，按行或句子分割。
    """
    if len(content.encode("utf-8")) <= MAX_CHUNK_BYTES:
        return [content]

    # 简单按行分割
    lines = content.split("\n")
    chunks = []
    current_chunk = ""
    current_size = 0

    for line in lines:
        line_size = len(line.encode("utf-8"))
        if current_size + line_size > MAX_CHUNK_BYTES:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line
            current_size = line_size
        else:
            current_chunk = current_chunk + "\n" + line if current_chunk else line
            current_size += line_size

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
