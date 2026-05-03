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

"""SkillInstaller - 远程 Skill 安装

从远程 Git 仓库克隆后，将 Skill 复制到本地并重建索引。
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from ascend_op_agent.skills.models import SkillInfo
from ascend_op_agent.skills.repository import SkillRepository
from ascend_op_agent.skills.storage import SkillStorage

logger = logging.getLogger(__name__)


class SkillInstaller:
    """Skill 安装器

    将远程仓库中的 Skill 复制到本地 skills 目录并重建索引。
    """

    def __init__(self, storage: Optional[SkillStorage] = None):
        """
        Args:
            storage: SkillStorage 实例，默认使用本地存储
        """
        self.storage = storage or SkillStorage()

    def install_skills(
        self,
        skills: list[SkillInfo],
        repo_path: Path,
        index: "SkillIndex",
    ) -> dict[str, bool]:
        """安装选中的 Skills 到本地目录

        Args:
            skills: 要安装的 SkillInfo 列表
            repo_path: 克隆后的缓存目录根路径
            index: SkillIndex 实例，用于重建索引

        Returns:
            安装结果字典 {skill_name: success}
        """
        results: dict[str, bool] = {}

        for skill_info in skills:
            src_path = Path(skill_info.path)
            dest_name = src_path.name
            dest_path = self.storage.skills_dir / dest_name

            # 覆盖安装前警告
            if dest_path.exists():
                logger.warning(f"覆盖已有 skill: {dest_name}")

            try:
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                results[dest_name] = True
                logger.info(f"成功安装 skill: {dest_name}")
            except Exception as e:
                logger.error(f"安装失败 {dest_name}: {e}")
                results[dest_name] = False

        # 重建索引（使用缓存目录创建 SkillRepository）
        if any(results.values()):
            try:
                repo = SkillRepository(local_skills_dir=str(repo_path))
                index.rebuild_index(repo)
                logger.info("Skill 索引重建完成")
            except Exception as e:
                logger.error(f"索引重建失败: {e}")

        return results
