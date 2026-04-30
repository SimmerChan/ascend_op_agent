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

"""SkillStorage - Skill本地存储

支持混合维度组织结构:
- 基础模板: {skill_name}/
- Bugfix: {skill_name}_bugfix/
- 性能优化: {skill_name}_performance/
"""

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from ascend_op_agent.skills.models import Skill

logger = logging.getLogger(__name__)

# 混合维度后缀
SUFFIX_BUGFIX = "_bugfix"
SUFFIX_PERFORMANCE = "_performance"


class SkillStorage:
    """Skill本地存储

    管理skill的本地文件系统存储，支持混合维度组织。
    """

    def __init__(
        self,
        skills_dir: Optional[str] = None,
    ):
        """
        Args:
            skills_dir: skills根目录
        """
        self.skills_dir = Path(
            skills_dir or os.path.expanduser("~/.ascend_op_agent/skills")
        )
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def save_skill(self, skill: Skill, dimension: str = "template") -> str:
        """保存Skill到本地

        Args:
            skill: Skill对象
            dimension: 维度 ("template", "bugfix", "performance")

        Returns:
            保存的路径
        """
        # 构建目录名
        if dimension == "bugfix":
            dir_name = f"{skill.name}{SUFFIX_BUGFIX}"
        elif dimension == "performance":
            dir_name = f"{skill.name}{SUFFIX_PERFORMANCE}"
        else:
            dir_name = skill.name

        skill_dir = self.skills_dir / dir_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # 保存SKILL.md
        skill_file = skill_dir / "SKILL.md"
        content = self._build_skill_content(skill)
        with open(skill_file, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Saved skill to {skill_dir}")
        return str(skill_dir)

    def _build_skill_content(self, skill: Skill) -> str:
        """构建SKILL.md内容

        Args:
            skill: Skill对象

        Returns:
            SKILL.md格式的字符串
        """
        lines = [
            "---",
            f"name: {skill.name}",
            f"description: {skill.description}",
        ]

        if skill.version:
            lines.append(f"version: {skill.version}")

        if skill.author:
            lines.append(f"author: {skill.author}")

        if skill.platforms:
            lines.append(f"platforms: [{', '.join(skill.platforms)}]")

        if skill.tags:
            lines.append(f"tags: [{', '.join(skill.tags)}]")

        if skill.prerequisites:
            lines.append("prerequisites:")
            for key, value in skill.prerequisites.items():
                if isinstance(value, list):
                    lines.append(f"  {key}: [{', '.join(value)}]")
                else:
                    lines.append(f"  {key}: {value}")

        if skill.metadata:
            lines.append("metadata:")
            lines.append("  ascend_op_agent:")
            for key, value in skill.metadata.items():
                if isinstance(value, list):
                    lines.append(f"    {key}: [{', '.join(str(v) for v in value)}]")
                else:
                    lines.append(f"    {key}: {value}")

        lines.append("---")
        lines.append("")
        lines.append(f"# {skill.name}")
        lines.append("")
        lines.append(skill.content)

        return "\n".join(lines)

    def load_skill(self, name: str) -> Optional[Skill]:
        """从本地加载Skill

        Args:
            name: skill名称（支持带后缀的完整目录名）

        Returns:
            Skill对象或None
        """
        skill_dir = self.skills_dir / name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            return None

        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()

            return self._parse_skill_content(name, content, str(skill_dir))

        except Exception as e:
            logger.warning(f"Failed to load skill {name}: {e}")
            return None

    def _parse_skill_content(
        self,
        name: str,
        content: str,
        local_path: str,
    ) -> Optional[Skill]:
        """解析SKILL.md内容"""
        import re

        from yaml import SafeLoader
        from yaml import load

        # 解析frontmatter
        pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)$"
        match = re.match(pattern, content, re.DOTALL)

        if not match:
            return None

        try:
            frontmatter = load(match.group(1), Loader=SafeLoader) or {}
            body = match.group(2)

            return Skill(
                name=frontmatter.get("name", name),
                description=frontmatter.get("description", ""),
                content=body,
                tags=frontmatter.get("tags", []),
                version=frontmatter.get("version"),
                author=frontmatter.get("author"),
                platforms=frontmatter.get("platforms", []),
                prerequisites=frontmatter.get("prerequisites", {}),
                metadata=frontmatter.get("metadata", {}),
                local_path=local_path,
            )

        except Exception as e:
            logger.warning(f"Failed to parse skill content: {e}")
            return None

    def delete_skill(self, name: str) -> bool:
        """删除Skill

        Args:
            name: skill名称

        Returns:
            是否成功删除
        """
        skill_dir = self.skills_dir / name

        if not skill_dir.exists():
            return False

        try:
            shutil.rmtree(skill_dir)
            logger.info(f"Deleted skill {name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete skill {name}: {e}")
            return False

    def list_skills(self) -> list[str]:
        """列出所有已安装的skill名称

        Returns:
            skill名称列表
        """
        skills = []
        if not self.skills_dir.exists():
            return skills

        for item in self.skills_dir.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                skills.append(item.name)

        return sorted(skills)

    def skill_exists(self, name: str) -> bool:
        """检查skill是否存在

        Args:
            name: skill名称

        Returns:
            是否存在
        """
        return (self.skills_dir / name / "SKILL.md").exists()

    def copy_skill(self, src_name: str, dest_name: str) -> Optional[str]:
        """复制skill

        Args:
            src_name: 源skill名称
            dest_name: 目标skill名称

        Returns:
            目标路径或None
        """
        src_dir = self.skills_dir / src_name
        dest_dir = self.skills_dir / dest_name

        if not src_dir.exists():
            return None

        try:
            shutil.copytree(src_dir, dest_dir)
            logger.info(f"Copied skill {src_name} to {dest_name}")
            return str(dest_dir)
        except Exception as e:
            logger.warning(f"Failed to copy skill: {e}")
            return None

    def rename_skill(self, old_name: str, new_name: str) -> bool:
        """重命名skill

        Args:
            old_name: 旧名称
            new_name: 新名称

        Returns:
            是否成功
        """
        src_dir = self.skills_dir / old_name
        dest_dir = self.skills_dir / new_name

        if not src_dir.exists():
            return False

        try:
            shutil.move(str(src_dir), str(dest_dir))
            logger.info(f"Renamed skill {old_name} to {new_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to rename skill: {e}")
            return False
