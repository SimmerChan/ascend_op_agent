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

"""VectorStore单元测试"""

import tempfile

import pytest

from ascend_op_agent.memory.vector_store import VectorStore


class TestVectorStore:
    """VectorStore测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            assert store.persist_dir.exists()

    def test_add_and_search_skill_vectors(self):
        """测试添加和搜索Skill向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            # 添加测试向量
            embedding = [0.1] * 384
            metadata = {"name": "test-skill", "description": "A test skill"}

            store.add_skill_vector("skill-1", embedding, metadata)

            # 搜索
            results = store.search_skill_vectors(embedding, k=1)

            assert len(results) == 1
            assert results[0]["id"] == "skill-1"
            assert results[0]["metadata"]["name"] == "test-skill"

    def test_add_skill_vectors_batch(self):
        """测试批量添加Skill向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            ids = ["skill-1", "skill-2", "skill-3"]
            embeddings = [[0.1] * 384, [0.2] * 384, [0.3] * 384]
            metadatas = [
                {"name": "skill-1"},
                {"name": "skill-2"},
                {"name": "skill-3"},
            ]

            store.add_skill_vectors(ids, embeddings, metadatas)

            assert store.get_skill_count() == 3

    def test_delete_skill_vector(self):
        """测试删除Skill向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            embedding = [0.1] * 384
            store.add_skill_vector("skill-1", embedding)

            store.delete_skill_vector("skill-1")

            assert store.get_skill_count() == 0

    def test_update_skill_vector(self):
        """测试更新Skill向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            embedding = [0.1] * 384
            store.add_skill_vector("skill-1", embedding, {"version": "1.0.0"})

            new_embedding = [0.5] * 384
            store.update_skill_vector("skill-1", new_embedding, {"version": "2.0.0"})

            results = store.search_skill_vectors(new_embedding, k=1)
            assert results[0]["metadata"]["version"] == "2.0.0"

    def test_search_with_filter(self):
        """测试带过滤条件的搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            store.add_skill_vectors(
                ["skill-1", "skill-2"],
                [[0.1] * 384, [0.2] * 384],
                [
                    {"name": "skill-1", "tag": "test"},
                    {"name": "skill-2", "tag": "example"},
                ],
            )

            results = store.search_skill_vectors(
                [0.1] * 384,
                k=5,
                filter_metadata={"tag": "test"},
            )

            assert len(results) == 1
            assert results[0]["id"] == "skill-1"

    def test_add_and_search_memory_vectors(self):
        """测试添加和搜索记忆向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            embedding = [0.1] * 384
            metadata = {"session_id": "test-session", "content": "test memory"}

            store.add_memory_vector("memory-1", embedding, metadata)

            results = store.search_memory_vectors(embedding, k=1)

            assert len(results) == 1
            assert results[0]["id"] == "memory-1"

    def test_get_memory_count(self):
        """测试获取记忆向量数量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            store.add_memory_vector("memory-1", [0.1] * 384)
            store.add_memory_vector("memory-2", [0.2] * 384)

            assert store.get_memory_count() == 2

    def test_clear_skills(self):
        """测试清空所有Skill向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            store.add_skill_vectors(
                ["skill-1", "skill-2"],
                [[0.1] * 384, [0.2] * 384],
            )

            store.clear_skills()

            assert store.get_skill_count() == 0

    def test_clear_memories(self):
        """测试清空所有记忆向量"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            store.add_memory_vector("memory-1", [0.1] * 384)
            store.add_memory_vector("memory-2", [0.2] * 384)

            store.clear_memories()

            assert store.get_memory_count() == 0

    def test_reset(self):
        """测试重置所有数据"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            store.add_skill_vector("skill-1", [0.1] * 384)
            store.add_memory_vector("memory-1", [0.2] * 384)

            store.reset()

            assert store.get_skill_count() == 0
            assert store.get_memory_count() == 0

    def test_get_collection(self):
        """测试获取指定collection"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            collection = store.get_collection("skills")

            assert collection is not None

    def test_get_skills_collection(self):
        """测试获取Skill向量collection"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            collection = store.get_skills_collection()

            assert collection is not None
            assert store.collection_skills in [c.name for c in store._client.list_collections()]

    def test_get_memories_collection(self):
        """测试获取记忆向量collection"""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir)

            collection = store.get_memories_collection()

            assert collection is not None
            assert store.collection_memories in [c.name for c in store._client.list_collections()]

    def test_persistence(self):
        """测试数据持久化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 第一次写入
            store1 = VectorStore(persist_dir=tmpdir)
            store1.add_skill_vector("skill-1", [0.1] * 384)
            store1.add_memory_vector("memory-1", [0.2] * 384)

            # 第二次读取相同目录
            store2 = VectorStore(persist_dir=tmpdir)

            assert store2.get_skill_count() == 1
            assert store2.get_memory_count() == 1
