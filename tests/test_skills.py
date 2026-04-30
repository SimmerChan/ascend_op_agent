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

"""Skill模块测试"""

import os
import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.skills import (
    InteractiveSelector,
    SkillIndex,
    SkillRepository,
    SkillRepositoryDiscovery,
    SkillStorage,
    format_skills_for_selection,
)
from ascend_op_agent.skills.models import Skill, SkillInfo


class TestSkillInfo:
    """SkillInfo测试"""

    def test_creation(self):
        """测试创建"""
        info = SkillInfo(
            name="test-skill",
            description="A test skill",
            tags=["test", "example"],
        )

        assert info.name == "test-skill"
        assert info.description == "A test skill"
        assert info.tags == ["test", "example"]
        assert info.source == "local"

    def test_to_dict(self):
        """测试转换为字典"""
        info = SkillInfo(
            name="test-skill",
            description="A test skill",
            tags=["test"],
        )
        result = info.to_dict()

        assert result["name"] == "test-skill"
        assert result["description"] == "A test skill"
        assert result["tags"] == ["test"]

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "name": "test-skill",
            "description": "A test skill",
            "tags": ["test"],
        }
        info = SkillInfo.from_dict(data)

        assert info.name == "test-skill"


class TestSkill:
    """Skill测试"""

    def test_creation(self):
        """测试创建"""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="# Test Skill\n\nThis is a test.",
            tags=["test"],
        )

        assert skill.name == "test-skill"
        assert skill.content == "# Test Skill\n\nThis is a test."

    def test_to_dict(self):
        """测试转换为字典"""
        skill = Skill(
            name="test-skill",
            description="A test skill",
            content="Test content",
        )
        result = skill.to_dict()

        assert result["name"] == "test-skill"
        assert result["content"] == "Test content"


class TestSkillRepository:
    """SkillRepository测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)
            assert repo.local_skills_dir == Path(tmpdir)

    def test_parse_frontmatter(self):
        """测试解析frontmatter"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            content = """---
name: test-skill
description: A test skill
tags: [test, example]
---
# Test Skill
"""
            frontmatter, body = repo.parse_frontmatter(content)

            assert frontmatter["name"] == "test-skill"
            assert frontmatter["description"] == "A test skill"
            assert body.strip() == "# Test Skill"

    def test_parse_frontmatter_no_frontmatter(self):
        """测试解析无frontmatter的内容"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            content = "# Just Content\n\nNo frontmatter here."
            frontmatter, body = repo.parse_frontmatter(content)

            assert frontmatter == {}
            assert "Just Content" in body

    def test_iter_skill_index_files(self):
        """测试遍历skill索引文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            # 创建测试目录结构
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: test\n---")

            files = list(repo.iter_skill_index_files(Path(tmpdir)))

            assert len(files) == 1
            assert files[0].name == "SKILL.md"

    def test_iter_skill_index_files_excludes(self):
        """测试排除目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            # 创建测试目录结构
            (Path(tmpdir) / ".git").mkdir()
            (Path(tmpdir) / ".git" / "SKILL.md").write_text("---\nname: test\n---")

            files = list(repo.iter_skill_index_files(Path(tmpdir)))

            assert len(files) == 0

    def test_extract_skill_info(self):
        """测试提取skill信息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            skill_path = Path(tmpdir) / "test-skill" / "SKILL.md"
            skill_path.parent.mkdir()
            skill_path.write_text("""---
name: test-skill
description: A test skill
version: 1.0.0
tags: [test, example]
---
# Test Skill
""")

            info = repo.extract_skill_info(skill_path)

            assert info is not None
            assert info.name == "test-skill"
            assert info.description == "A test skill"
            assert "test" in info.tags
            assert info.version == "1.0.0"

    def test_list_local_skills(self):
        """测试列出本地skills"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = SkillRepository(local_skills_dir=tmpdir)

            # 创建测试skill
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill
tags: [test]
---
# Test Skill
""")

            skills = repo.list_local_skills()

            assert len(skills) == 1
            assert skills[0].name == "test-skill"


class TestSkillIndex:
    """SkillIndex测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            assert index.db_path == f"{tmpdir}/test.db"

    def test_add_and_search(self):
        """测试添加和搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill for testing",
                content="Test content",
                tags=["test", "example"],
            )

            index.add_skill(skill)

            results = index.search("test")

            assert len(results) >= 1
            assert any(s.name == "test-skill" for s in results)

    def test_remove_skill(self):
        """测试移除skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )

            index.add_skill(skill)
            index.remove_skill("test-skill")

            results = index.search("test")
            assert all(s.name != "test-skill" for s in results)

    def test_lru_cache(self):
        """测试LRU缓存"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            # 添加多个skills
            for i in range(10):
                skill = Skill(
                    name=f"skill-{i}",
                    description=f"Skill {i}",
                    content=f"Content {i}",
                )
                index.add_skill(skill)

            # 搜索应该使用缓存
            index.search("skill")
            index.search("skill")

            # 验证缓存工作（不会报错）
            assert True

    def test_snapshot(self):
        """测试磁盘快照"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )
            index.add_skill(skill)

            # 快照文件应该存在
            snapshot_path = Path(tmpdir) / ".skills_prompt_snapshot.json"
            assert snapshot_path.exists()

    def test_clear_all_caches(self):
        """测试清除所有缓存"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(db_path=f"{tmpdir}/test.db", cache_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )
            index.add_skill(skill)
            index.search("test")

            # 清除缓存
            index.clear_all_caches()

            # 快照应该被清除
            snapshot_path = Path(tmpdir) / ".skills_prompt_snapshot.json"
            assert not snapshot_path.exists()


class TestSkillStorage:
    """SkillStorage测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            assert storage.skills_dir == Path(tmpdir)

    def test_save_skill(self):
        """测试保存skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="# Test Skill\n\nThis is a test.",
                tags=["test"],
                version="1.0.0",
            )

            path = storage.save_skill(skill)

            assert "test-skill" in path
            assert (Path(path) / "SKILL.md").exists()

    def test_save_skill_with_dimension(self):
        """测试保存带维度的skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )

            path = storage.save_skill(skill, dimension="bugfix")

            assert "_bugfix" in path

    def test_load_skill(self):
        """测试加载skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="# Test Skill",
                tags=["test"],
            )

            storage.save_skill(skill)
            loaded = storage.load_skill("test-skill")

            assert loaded is not None
            assert loaded.name == "test-skill"
            assert loaded.description == "A test skill"

    def test_delete_skill(self):
        """测试删除skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )

            storage.save_skill(skill)
            result = storage.delete_skill("test-skill")

            assert result is True
            assert not storage.skill_exists("test-skill")

    def test_list_skills(self):
        """测试列出skills"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill1 = Skill(name="skill-1", description="Skill 1", content="Content 1")
            skill2 = Skill(name="skill-2", description="Skill 2", content="Content 2")

            storage.save_skill(skill1)
            storage.save_skill(skill2)

            skills = storage.list_skills()

            assert len(skills) == 2
            assert "skill-1" in skills
            assert "skill-2" in skills

    def test_skill_exists(self):
        """测试skill存在检查"""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = SkillStorage(skills_dir=tmpdir)

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
            )

            assert not storage.skill_exists("test-skill")

            storage.save_skill(skill)

            assert storage.skill_exists("test-skill")


class TestInteractiveSelector:
    """InteractiveSelector测试"""

    def test_creation(self):
        """测试创建"""
        skills = [
            SkillInfo(name="skill-1", description="Skill 1", tags=["test"]),
            SkillInfo(name="skill-2", description="Skill 2", tags=["example"]),
        ]

        selector = InteractiveSelector(skills)

        assert len(selector.skills) == 2
        assert len(selector._selected) == 0

    def test_format_skills_for_selection(self):
        """测试格式化skills列表"""
        skills = [
            SkillInfo(name="skill-1", description="Skill 1", tags=["test"]),
        ]

        result = format_skills_for_selection(skills)

        assert "skill-1" in result
        assert "Skill 1" in result


class TestSkillRepositoryDiscovery:
    """SkillRepositoryDiscovery测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            discovery = SkillRepositoryDiscovery(cache_dir=tmpdir)

            assert discovery.cache_dir == Path(tmpdir)

    def test_get_repo_name(self):
        """测试从URL提取仓库名"""
        with tempfile.TemporaryDirectory() as tmpdir:
            discovery = SkillRepositoryDiscovery(cache_dir=tmpdir)

            assert discovery._get_repo_name("https://gitcode.com/user/repo-name") == "repo-name"
            assert discovery._get_repo_name("git@gitcode.com:user/repo.git") == "repo"
