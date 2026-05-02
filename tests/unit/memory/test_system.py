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

"""MemorySystem单元测试"""

import tempfile

import pytest

from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.memory.episodic_memory import EpisodicMemory
from ascend_op_agent.memory.semantic_memory import SemanticMemory
from ascend_op_agent.memory.system import MemorySystem
from ascend_op_agent.skills import SkillIndex
from ascend_op_agent.skills.models import Skill


class TestMemorySystem:
    """MemorySystem测试"""

    def test_creation(self):
        """测试创建"""
        system = MemorySystem()

        assert system._working_memory is not None
        assert system._episodic_memory is not None
        assert system._semantic_memory is not None

    def test_creation_with_custom_instances(self):
        """测试使用自定义实例创建"""
        working = MemoryStore()
        episodic = EpisodicMemory()
        semantic = SemanticMemory()

        system = MemorySystem(
            working_memory=working,
            episodic_memory=episodic,
            semantic_memory=semantic,
        )

        assert system._working_memory is working
        assert system._episodic_memory is episodic
        assert system._semantic_memory is semantic

    # ==================== Working Memory Tests ====================

    def test_add_working_memory(self):
        """测试添加工作记忆"""
        system = MemorySystem()

        system.add_working_memory("memory", "Test memory")

        assert "Test memory" in system.get_working_memory("memory")

    def test_get_working_memory(self):
        """测试获取工作记忆"""
        system = MemorySystem()

        system.add_working_memory("memory", "Memory 1")
        system.add_working_memory("memory", "Memory 2")

        memories = system.get_working_memory("memory")

        assert len(memories) == 2
        assert "Memory 1" in memories
        assert "Memory 2" in memories

    def test_format_working_memory(self):
        """测试格式化工作记忆"""
        system = MemorySystem()

        system.add_working_memory("memory", "Test 1")
        system.add_working_memory("memory", "Test 2")

        formatted = system.format_working_memory("memory")

        assert "MEMORY" in formatted
        assert "Test 1" in formatted
        assert "Test 2" in formatted

    # ==================== Episodic Memory Tests ====================

    def test_start_episode(self):
        """测试开始会话片段"""
        system = MemorySystem()

        episode_id = system.start_episode()

        assert episode_id.startswith("episode_")

    def test_start_episode_with_custom_id(self):
        """测试使用自定义ID开始会话片段"""
        system = MemorySystem()

        episode_id = system.start_episode("my-episode")

        assert episode_id == "my-episode"

    def test_add_episode_turn(self):
        """测试添加对话轮次"""
        system = MemorySystem()
        system.start_episode("test-episode")

        system.add_episode_turn("user", "Hello")
        system.add_episode_turn("assistant", "Hi there")

        episode = system.get_current_episode()

        assert episode is not None
        assert episode.message_count == 2

    def test_end_episode(self):
        """测试结束会话片段"""
        system = MemorySystem()
        system.start_episode("test-episode")
        system.add_episode_turn("user", "Hello")

        episode = system.end_episode()

        assert episode is not None
        assert episode.episode_id == "test-episode"
        assert system.get_current_episode() is None

    # ==================== Semantic Memory Tests ====================

    def test_search_skills(self):
        """测试搜索Skill"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )
            semantic = SemanticMemory(skill_index=skill_index)
            system = MemorySystem(semantic_memory=semantic)

            skill = Skill(
                name="matmul-template",
                description="Matrix multiplication template",
                content="This is a template for matmul operator",
                tags=["matmul", "template"],
            )
            skill_index.add_skill(skill)

            # 使用FTS5搜索
            results = system.search_skills("matmul", k=5, use_hybrid=False)

            assert len(results) >= 1

    def test_get_skill_recommendations(self):
        """测试获取Skill推荐"""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )
            semantic = SemanticMemory(skill_index=skill_index)
            system = MemorySystem(semantic_memory=semantic)

            skill = Skill(
                name="matmul-template",
                description="Matrix multiplication template",
                content="Template for matmul operator",
                tags=["matmul", "template"],
            )
            skill.metadata = {"op_type": "matmul"}
            skill_index.add_skill(skill)

            recommendations = system.get_skill_recommendations(
                current_op_type="matmul",
                current_task="development",
                k=3,
            )

            assert isinstance(recommendations, list)

    # ==================== Unified Retrieve Tests ====================

    def test_retrieve_all_layers(self):
        """测试统一检索所有层"""
        system = MemorySystem()

        # 添加工作记忆
        system.add_working_memory("memory", "Previous work on matmul operator")

        results = system.retrieve("matmul", k=5)

        assert "working" in results
        assert "episodic" in results
        assert "semantic" in results

    def test_retrieve_specific_layers(self):
        """测试检索指定层"""
        system = MemorySystem()

        results = system.retrieve("test", layers=["working"], k=5)

        assert "working" in results
        assert "episodic" not in results
        assert "semantic" not in results

    def test_retrieve_working_memory(self):
        """测试从WorkingMemory检索"""
        system = MemorySystem()

        system.add_working_memory("memory", "Previous work on matmul operator")
        system.add_working_memory("memory", "Some other work")

        results = system.retrieve("matmul", layers=["working"], k=5)

        working_results = results["working"]
        assert len(working_results) >= 1
        assert any("matmul" in r for r in working_results)

    # ==================== Session Management Tests ====================

    def test_clear_working_memory(self):
        """测试清除工作记忆"""
        system = MemorySystem()

        system.add_working_memory("memory", "Memory 1")
        system.add_working_memory("user", "User pref")

        system.clear_working_memory("memory")

        assert len(system.get_working_memory("memory")) == 0
        assert len(system.get_working_memory("user")) == 1

    def test_clear_all_working_memory(self):
        """测试清除所有工作记忆"""
        system = MemorySystem()

        system.add_working_memory("memory", "Memory 1")
        system.add_working_memory("user", "User pref")

        system.clear_working_memory()

        assert len(system.get_working_memory("memory")) == 0
        assert len(system.get_working_memory("user")) == 0

    def test_get_current_episode(self):
        """测试获取当前会话片段"""
        system = MemorySystem()
        system.start_episode("test-episode")

        episode = system.get_current_episode()

        assert episode is not None
        assert episode.episode_id == "test-episode"

    def test_get_current_episode_when_none(self):
        """测试没有当前会话片段时返回None"""
        system = MemorySystem()

        assert system.get_current_episode() is None
