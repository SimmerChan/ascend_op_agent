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

"""SkillInstaller 测试"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascend_op_agent.skills.installer import SkillInstaller
from ascend_op_agent.skills.models import SkillInfo


class TestSkillInstaller:
    """SkillInstaller 测试"""

    def test_install_single_skill_success(self, tmp_path):
        """测试安装单个 Skill 成功"""
        # 创建临时缓存目录和 skill 目录
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        skill_dir = cache_dir / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test_skill\ndescription: Test skill\n---\n# Test\n",
            encoding="utf-8",
        )

        # 创建临时本地 skills 目录
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))

        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        skill_infos = [
            SkillInfo(
                name="test_skill",
                description="Test skill",
                path=str(skill_dir),
            )
        ]

        results = installer.install_skills(skill_infos, cache_dir, mock_index)

        assert results["test_skill"] is True
        assert (skills_dir / "test_skill" / "SKILL.md").exists()
        mock_index.rebuild_index.assert_called_once()

    def test_install_multiple_skills_success(self, tmp_path):
        """测试安装多个 Skills 成功"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # 创建两个 skill 目录
        for name in ["skill_a", "skill_b"]:
            skill_dir = cache_dir / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name}\n---\n# {name}\n",
                encoding="utf-8",
            )

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))
        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        skill_infos = [
            SkillInfo(name="skill_a", description="A", path=str(cache_dir / "skill_a")),
            SkillInfo(name="skill_b", description="B", path=str(cache_dir / "skill_b")),
        ]

        results = installer.install_skills(skill_infos, cache_dir, mock_index)

        assert results["skill_a"] is True
        assert results["skill_b"] is True
        assert (skills_dir / "skill_a" / "SKILL.md").exists()
        assert (skills_dir / "skill_b" / "SKILL.md").exists()

    def test_override_install_warns(self, tmp_path):
        """测试覆盖安装时记录警告"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        # 在目标目录中创建一个已存在的 skill
        existing = skills_dir / "existing_skill"
        existing.mkdir()
        (existing / "SKILL.md").write_text(
            "---\nname: existing_skill\ndescription: Existing\n---\n# Old\n",
            encoding="utf-8",
        )

        # 在缓存目录中创建同名 skill
        skill_dir = cache_dir / "existing_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: existing_skill\ndescription: Existing\n---\n# New\n",
            encoding="utf-8",
        )

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))
        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        skill_infos = [
            SkillInfo(
                name="existing_skill",
                description="Existing skill",
                path=str(skill_dir),
            )
        ]

        with patch("ascend_op_agent.skills.installer.logger") as mock_logger:
            results = installer.install_skills(skill_infos, cache_dir, mock_index)

        assert results["existing_skill"] is True
        mock_logger.warning.assert_called_once()
        call_args = str(mock_logger.warning.call_args)
        assert "覆盖已有 skill" in call_args or "existing_skill" in call_args

    def test_invalid_source_path_handled(self, tmp_path):
        """测试源路径无效时记录错误"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))
        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        skill_infos = [
            SkillInfo(
                name="nonexistent",
                description="Nonexistent skill",
                path=str(cache_dir / "nonexistent"),
            )
        ]

        with patch("ascend_op_agent.skills.installer.logger") as mock_logger:
            results = installer.install_skills(skill_infos, cache_dir, mock_index)

        assert results["nonexistent"] is False
        mock_logger.error.assert_called()

    def test_empty_skill_list(self, tmp_path):
        """测试空 skill 列表"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))
        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        results = installer.install_skills([], cache_dir, mock_index)

        assert results == {}
        mock_index.rebuild_index.assert_not_called()

    def test_partial_failure(self, tmp_path):
        """测试部分成功部分失败"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        # 创建一个有效的 skill
        valid_dir = cache_dir / "valid_skill"
        valid_dir.mkdir()
        (valid_dir / "SKILL.md").write_text(
            "---\nname: valid_skill\ndescription: Valid\n---\n# Valid\n",
            encoding="utf-8",
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        from ascend_op_agent.skills.storage import SkillStorage
        storage = SkillStorage(str(skills_dir))
        mock_index = MagicMock()

        installer = SkillInstaller(storage=storage)
        skill_infos = [
            SkillInfo(
                name="valid_skill",
                description="Valid skill",
                path=str(valid_dir),
            ),
            SkillInfo(
                name="invalid_skill",
                description="Invalid skill",
                path=str(cache_dir / "invalid_skill"),
            ),
        ]

        with patch("ascend_op_agent.skills.installer.logger"):
            results = installer.install_skills(skill_infos, cache_dir, mock_index)

        assert results["valid_skill"] is True
        assert results["invalid_skill"] is False
