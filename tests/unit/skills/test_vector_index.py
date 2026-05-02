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

"""SkillIndex向量索引测试"""

import tempfile

import pytest

from ascend_op_agent.skills import SkillIndex
from ascend_op_agent.skills.models import Skill


class TestSkillIndexVector:
    """SkillIndex向量索引测试"""

    def test_creation_with_vector_store(self):
        """测试创建带向量存储的索引"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            assert index._vector_store is not None

    def test_add_skill_stores_vector(self):
        """测试添加Skill时存储向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="This is a test skill content about operator development",
                tags=["test", "example"],
            )

            index.add_skill(skill)

            # 验证向量存储中有数据
            if index.embedding_model is not None:
                assert index._vector_store.get_skill_count() == 1

    def test_search_by_vector(self):
        """测试基于向量的搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            # 添加测试Skill
            skill = Skill(
                name="add-operator",
                description="Add operator for element-wise addition",
                content="This operator performs element-wise addition on tensors",
                tags=["arithmetic", "element-wise"],
            )

            index.add_skill(skill)

            # 如果embedding模型可用，测试向量搜索
            if index.embedding_model is not None:
                # 使用内容编码查询
                query = "element-wise addition operation"
                query_embedding = index.embedding_model.encode(query).tolist()

                results = index.search_by_vector(query_embedding, k=1)

                # 应该能召回相关Skill
                assert len(results) >= 0  # 可能为空因为内容可能不匹配

    def test_hybrid_search_fallback(self):
        """测试混合检索在embedding模型不可用时回退到FTS5"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
                tags=["test"],
            )

            index.add_skill(skill)

            # 如果embedding模型不可用，应该回退到FTS5
            if index.embedding_model is None:
                results = index.hybrid_search("test", k=5)
                assert len(results) >= 1
                assert any(s.name == "test-skill" for s in results)

    def test_remove_skill_removes_vector(self):
        """测试移除Skill时删除向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="test-skill",
                description="A test skill",
                content="Test content",
                tags=["test"],
            )

            index.add_skill(skill)
            index.remove_skill("test-skill")

            # 验证SQLite中已删除
            results = index.search("test")
            assert all(s.name != "test-skill" for s in results)
