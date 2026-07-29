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

"""SkillRepository - Skill仓库管理

参考Hermes Agent实现，支持:
- 本地Skill目录扫描
- 外部Git仓库克隆和同步
- SKILL.md格式解析
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ascend_op_agent.skills.models import Skill, SkillInfo

logger = logging.getLogger(__name__)

# SKILL.md文件名
SKILL_INDEX_FILENAME = "SKILL.md"

# 排除的目录
EXCLUDED_DIRS = {".git", ".github", ".hub", "__pycache__", ".pytest_cache"}


class SkillRepository:
    """Skill仓库管理

    管理本地和外部Skill仓库，支持克隆、更新和Skill列表获取。
    """

    def __init__(
        self,
        local_skills_dir: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        """
        Args:
            local_skills_dir: 本地skills根目录
            cache_dir: 缓存目录（用于存储克隆的远程仓库）
        """
        self.local_skills_dir = Path(
            local_skills_dir or os.path.expanduser("~/.ascend_op_agent/skills")
        )
        self.cache_dir = Path(cache_dir or os.path.expanduser("~/.ascend_op_agent/.skill_cache"))

        # 确保目录存在
        self.local_skills_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_all_skills_dirs(self) -> list[Path]:
        """获取所有skill目录（本地优先）"""
        dirs = [self.local_skills_dir]
        return dirs

    def iter_skill_index_files(
        self,
        skills_dir: Path,
        filename: str = SKILL_INDEX_FILENAME,
    ):
        """遍历skills目录，yield匹配的文件路径

        Args:
            skills_dir: skills根目录
            filename: 要匹配的文件名
        """
        if not skills_dir.exists():
            return

        for root, dirs, files in os.walk(skills_dir):
            # 过滤排除的目录
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            root_path = Path(root)
            if filename in files:
                yield root_path / filename

    def parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """解析YAML frontmatter

        Args:
            content: 文件内容

        Returns:
            (frontmatter_dict, body_content)
        """
        # 支持CSafeLoader回退到普通Loader
        try:
            from yaml import CSafeLoader as SafeLoader
        except ImportError:
            from yaml import SafeLoader

        from yaml import load

        # 匹配 --- 包裹的frontmatter
        pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)$"
        match = re.match(pattern, content, re.DOTALL)

        if match:
            frontmatter_str = match.group(1)
            body = match.group(2)
            try:
                frontmatter = load(frontmatter_str, Loader=SafeLoader) or {}
            except Exception:
                frontmatter = {}
            return frontmatter, body

        return {}, content

    def extract_skill_info(self, skill_path: Path) -> Optional[SkillInfo]:
        """从SKILL.md提取Skill信息

        Args:
            skill_path: SKILL.md文件路径

        Returns:
            SkillInfo或None（如果解析失败）
        """
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()

            frontmatter, _ = self.parse_frontmatter(content)

            # 获取metadata.hermes字段
            metadata = frontmatter.get("metadata", {}) or {}
            hermes_meta = metadata.get("hermes", {}) if isinstance(metadata, dict) else {}

            # 获取tags
            tags = hermes_meta.get("tags", []) or frontmatter.get("tags", [])

            # 获取版本
            version = frontmatter.get("version", "1.0.0")

            return SkillInfo(
                name=frontmatter.get("name", skill_path.parent.name),
                description=frontmatter.get("description", ""),
                tags=tags if isinstance(tags, list) else [],
                version=version,
                source="local",
                path=str(skill_path.parent),
            )
        except Exception as e:
            logger.warning(f"Failed to parse skill info from {skill_path}: {e}")
            return None

    def load_skill(self, skill_path: Path) -> Optional[Skill]:
        """加载完整的Skill

        Args:
            skill_path: SKILL.md文件路径

        Returns:
            Skill或None
        """
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()

            frontmatter, body = self.parse_frontmatter(content)

            # 获取metadata.hermes字段
            metadata = frontmatter.get("metadata", {}) or {}
            hermes_meta = metadata.get("hermes", {}) if isinstance(metadata, dict) else {}

            # 获取tags
            tags = hermes_meta.get("tags", []) or frontmatter.get("tags", [])

            return Skill(
                name=frontmatter.get("name", skill_path.parent.name),
                description=frontmatter.get("description", ""),
                content=body,
                tags=tags if isinstance(tags, list) else [],
                version=frontmatter.get("version"),
                author=frontmatter.get("author"),
                platforms=frontmatter.get("platforms", []),
                prerequisites=frontmatter.get("prerequisites", {}),
                metadata=metadata,
                local_path=str(skill_path.parent),
            )
        except Exception as e:
            logger.warning(f"Failed to load skill from {skill_path}: {e}")
            return None

    def list_local_skills(self) -> list[SkillInfo]:
        """列出本地所有Skill"""
        skills = []
        for skills_dir in self.get_all_skills_dirs():
            for skill_index in self.iter_skill_index_files(skills_dir):
                skill_info = self.extract_skill_info(skill_index)
                if skill_info:
                    skills.append(skill_info)
        return skills

    def get_skill(self, name: str) -> Optional[Skill]:
        """根据名称获取Skill

        Args:
            name: skill名称

        Returns:
            Skill或None
        """
        for skills_dir in self.get_all_skills_dirs():
            for skill_index in self.iter_skill_index_files(skills_dir):
                skill_info = self.extract_skill_info(skill_index)
                if skill_info and skill_info.name == name:
                    return self.load_skill(skill_index)
        return None


class SkillRepositoryDiscovery:
    """远程Skill仓库发现

    支持从Git仓库动态获取skill列表。
    """

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Args:
            cache_dir: 缓存目录
        """
        self.cache_dir = Path(cache_dir or os.path.expanduser("~/.ascend_op_agent/.repo_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def clone_or_update(self, repo_url: str, branch: str = "main") -> Path:
        """克隆或更新仓库

        Args:
            repo_url: Git仓库URL
            branch: 分支名

        Returns:
            仓库本地路径
        """
        # 根据URL生成目录名
        repo_name = self._get_repo_name(repo_url)
        repo_path = self.cache_dir / repo_name

        if repo_path.exists():
            # 更新现有仓库
            self._git_pull(repo_path, branch)
        else:
            # 克隆新仓库
            self._git_clone(repo_url, repo_path, branch)

        return repo_path

    def _get_repo_name(self, repo_url: str) -> str:
        """从URL提取仓库名"""
        # 处理 https://gitcode.com/user/repo 和 git@gitcode.com:user/repo 格式
        if "/" in repo_url:
            parts = repo_url.rstrip("/").split("/")
            return parts[-1].replace(".git", "")
        return "unknown"

    def _git_clone(self, repo_url: str, dest_path: Path, branch: str = "main") -> None:
        """克隆仓库"""
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "-b", branch, repo_url, str(dest_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"Cloned repository to {dest_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone {repo_url}: {e.stderr}")
            raise

    def _git_pull(self, repo_path: Path, branch: str = "main") -> None:
        """更新仓库"""
        try:
            subprocess.run(
                ["git", "-C", str(repo_path), "pull", "origin", branch],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"Updated repository at {repo_path}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to pull {repo_path}: {e.stderr}")

    def fetch_skill_list(self, repo_url: str, branch: str = "main") -> list[SkillInfo]:
        """获取远程仓库中的skill列表

        Args:
            repo_url: Git仓库URL
            branch: 分支名

        Returns:
            SkillInfo列表
        """
        repo_path = self.clone_or_update(repo_url, branch)

        # 扫描仓库中的所有SKILL.md
        skills = []
        for root, dirs, files in os.walk(repo_path):
            # 过滤排除的目录
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            if SKILL_INDEX_FILENAME in files:
                skill_path = Path(root) / SKILL_INDEX_FILENAME
                skill_info = self._parse_remote_skill_info(skill_path, repo_url)
                if skill_info:
                    skills.append(skill_info)

        return skills

    def _parse_remote_skill_info(
        self,
        skill_path: Path,
        repo_url: str,
    ) -> Optional[SkillInfo]:
        """解析远程仓库中的skill信息

        Args:
            skill_path: SKILL.md文件路径
            repo_url: 仓库URL

        Returns:
            SkillInfo或None
        """
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 简单的frontmatter解析
            pattern = r"^---\s*\n(.*?)\n---\s*\n?(.*)$"
            match = re.match(pattern, content, re.DOTALL)

            if not match:
                return None

            frontmatter_str = match.group(1)

            # 简单解析YAML
            from yaml import SafeLoader
            from yaml import load

            try:
                frontmatter = load(frontmatter_str, Loader=SafeLoader) or {}
            except Exception:
                return None

            # 获取metadata.hermes字段
            metadata = frontmatter.get("metadata", {}) or {}
            hermes_meta = metadata.get("hermes", {}) if isinstance(metadata, dict) else {}

            # 获取tags
            tags = hermes_meta.get("tags", []) or frontmatter.get("tags", [])

            return SkillInfo(
                name=frontmatter.get("name", skill_path.parent.name),
                description=frontmatter.get("description", ""),
                tags=tags if isinstance(tags, list) else [],
                version=frontmatter.get("version", "1.0.0"),
                source=repo_url,
                path=str(skill_path.parent),
            )
        except Exception as e:
            logger.warning(f"Failed to parse remote skill info from {skill_path}: {e}")
            return None
