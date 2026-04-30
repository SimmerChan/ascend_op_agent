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

"""Skill数据模型"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Skill:
    """Skill完整数据模型"""
    name: str
    description: str
    content: str
    tags: list[str] = field(default_factory=list)
    version: Optional[str] = None
    author: Optional[str] = None
    platforms: list[str] = field(default_factory=list)
    prerequisites: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_repo: Optional[str] = None
    local_path: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "tags": self.tags,
            "version": self.version,
            "author": self.author,
            "platforms": self.platforms,
            "prerequisites": self.prerequisites,
            "metadata": self.metadata,
            "source_repo": self.source_repo,
            "local_path": self.local_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        """从字典创建"""
        return cls(
            name=data["name"],
            description=data["description"],
            content=data.get("content", ""),
            tags=data.get("tags", []),
            version=data.get("version"),
            author=data.get("author"),
            platforms=data.get("platforms", []),
            prerequisites=data.get("prerequisites", {}),
            metadata=data.get("metadata", {}),
            source_repo=data.get("source_repo"),
            local_path=data.get("local_path"),
        )


@dataclass
class SkillInfo:
    """Skill摘要信息（用于列表展示）"""
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    version: Optional[str] = None
    source: str = "local"
    path: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "version": self.version,
            "source": self.source,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SkillInfo":
        """从字典创建"""
        return cls(
            name=data["name"],
            description=data["description"],
            tags=data.get("tags", []),
            version=data.get("version"),
            source=data.get("source", "local"),
            path=data.get("path"),
        )


@dataclass
class SkillBundle:
    """Skill包（用于远程获取）"""
    name: str
    files: dict[str, str]
    source: str
    identifier: str
    trust_level: str = "community"
    metadata: dict[str, Any] = field(default_factory=dict)
