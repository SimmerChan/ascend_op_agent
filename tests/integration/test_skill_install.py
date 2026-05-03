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

"""Skill 安装流程集成测试

测试从远程仓库安装 Skill 的完整流程：
fetch → select → install → search
"""

import os
import tempfile
import shutil
from pathlib import Path

import pytest

from ascend_op_agent.skills.installer import SkillInstaller
from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.storage import SkillStorage
from ascend_op_agent.skills.repository import SkillRepository


class TestSkillInstallFlow:
    """Skill 安装流程集成测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.cache_dir = Path(self.temp_dir) / "cache"
        self.cache_dir.mkdir()
        self.local_skills_dir = Path(self.temp_dir) / "skills"
        self.local_skills_dir.mkdir()
        self.db_path = Path(self.temp_dir) / "skills_index.db"

    def teardown_method(self):
        """每个测试后清理"""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill_in_cache(self, name: str, content: str = None):
        """在缓存目录中创建一个 skill 目录"""
        skill_dir = self.cache_dir / name
        skill_dir.mkdir(exist_ok=True)
        if content is None:
            content = f"""---
name: {name}
description: {name} skill
tags: [test, {name}]
---

# {name}

This is the {name} skill.
"""
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        return skill_dir

    def test_fetch_install_search_flow(self):
        """测试完整的 fetch → install → search 流程"""
        # 1. 在缓存目录中创建测试 skills
        self._create_skill_in_cache("test_elementwise")
        self._create_skill_in_cache("test_reduction")

        # 2. 创建 SkillRepository 和 SkillIndex
        repo = SkillRepository(local_skills_dir=str(self.cache_dir))
        index = SkillIndex(
            db_path=str(self.db_path),
            cache_dir=str(self.temp_dir),
        )
        storage = SkillStorage(str(self.local_skills_dir))

        # 3. 验证 fetch 流程
        skills = repo.list_local_skills()
        assert len(skills) == 2
        skill_names = {s.name for s in skills}
        assert "test_elementwise" in skill_names
        assert "test_reduction" in skill_names

        # 4. 执行安装
        installer = SkillInstaller(storage=storage)
        skill_infos = [s for s in skills]  # 安装所有
        results = installer.install_skills(skill_infos, self.cache_dir, index)

        assert all(results.values()), f"所有安装应成功: {results}"

        # 5. 验证本地目录有 skill
        assert (self.local_skills_dir / "test_elementwise" / "SKILL.md").exists()
        assert (self.local_skills_dir / "test_reduction" / "SKILL.md").exists()

        # 6. 验证索引已更新，可以搜索到
        search_results = index.search("elementwise")
        assert len(search_results) >= 1
        assert any(r.name == "test_elementwise" for r in search_results)

    def test_install_empty_repository(self):
        """测试空仓库的处理"""
        repo = SkillRepository(local_skills_dir=str(self.cache_dir))
        skills = repo.list_local_skills()
        assert len(skills) == 0

    def test_install_overwrites_existing(self):
        """测试覆盖已安装的 skill"""
        # 1. 先安装一个 skill
        skill_dir = self._create_skill_in_cache("overwrite_test")
        installer = SkillInstaller(storage=SkillStorage(str(self.local_skills_dir)))
        index = SkillIndex(db_path=str(self.db_path), cache_dir=str(self.temp_dir))
        repo = SkillRepository(local_skills_dir=str(self.cache_dir))
        skills = repo.list_local_skills()
        installer.install_skills(skills, self.cache_dir, index)

        # 2. 修改本地 skill 的内容
        local_path = self.local_skills_dir / "overwrite_test" / "SKILL.md"
        local_path.write_text(
            "---\nname: overwrite_test\ndescription: Modified locally\n---\n# Modified\n",
            encoding="utf-8",
        )

        # 3. 再次安装（应该覆盖）
        self._create_skill_in_cache("overwrite_test")
        installer.install_skills(skills, self.cache_dir, index)

        # 4. 验证内容已被覆盖
        content = local_path.read_text(encoding="utf-8")
        assert "Modified locally" not in content
        assert "This is the overwrite_test skill" in content
