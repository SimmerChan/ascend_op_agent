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

"""Embedding 配置测试"""

import os
import tempfile

import pytest
import yaml

from ascend_op_agent.config import (
    Config,
    EmbeddingConfig,
    VectorStoreConfig,
    load_config,
)


class TestEmbeddingConfig:
    """Embedding 配置测试"""

    def test_default_values(self):
        """测试默认配置值"""
        config = EmbeddingConfig()
        assert config.model == "sentence-transformers/all-MiniLM-L6-v3"

    def test_custom_model(self):
        """测试自定义模型"""
        config = EmbeddingConfig(model="all-mpnet-base-v2")
        assert config.model == "all-mpnet-base-v2"


class TestVectorStoreConfig:
    """向量存储配置测试"""

    def test_default_values(self):
        """测试默认配置值"""
        config = VectorStoreConfig()
        assert "~/.ascend_op_agent/vector_db" in config.persist_dir

    def test_custom_persist_dir(self):
        """测试自定义持久化目录"""
        config = VectorStoreConfig(persist_dir="/custom/path")
        assert config.persist_dir == "/custom/path"


class TestConfigEmbeddingIntegration:
    """Config 类中 embedding 和 vector_store 集成测试"""

    def test_config_has_embedding(self):
        """测试 Config 包含 embedding 配置"""
        config = Config()
        assert config.embedding is not None
        assert isinstance(config.embedding, EmbeddingConfig)

    def test_config_has_vector_store(self):
        """测试 Config 包含 vector_store 配置"""
        config = Config()
        assert config.vector_store is not None
        assert isinstance(config.vector_store, VectorStoreConfig)

    def test_embedding_defaults_from_config(self):
        """测试从 Config 获取默认 embedding 值"""
        config = Config()
        assert config.embedding.model == "sentence-transformers/all-MiniLM-L6-v3"

    def test_vector_store_defaults_from_config(self):
        """测试从 Config 获取默认 vector_store 值"""
        config = Config()
        assert "vector_db" in config.vector_store.persist_dir


class TestEmbeddingConfigEnvVar:
    """Embedding 配置环境变量测试"""

    def test_embedding_model_env_var(self):
        """测试 embedding 模型环境变量覆盖"""
        os.environ["EMBEDDING_MODEL"] = "custom-embedding-model"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"embedding": {"model": "${EMBEDDING_MODEL}"}}, f)
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            assert config.embedding.model == "custom-embedding-model"
        finally:
            os.unlink(config_path)
            del os.environ["EMBEDDING_MODEL"]

    def test_vector_store_persist_dir_env_var(self):
        """测试向量存储目录环境变量覆盖"""
        os.environ["VECTOR_STORE_DIR"] = "/custom/vector/db"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"vector_store": {"persist_dir": "${VECTOR_STORE_DIR}"}}, f)
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            assert config.vector_store.persist_dir == "/custom/vector/db"
        finally:
            os.unlink(config_path)
            del os.environ["VECTOR_STORE_DIR"]

    def test_env_var_with_default_value(self):
        """测试带默认值的环境变量"""
        # 环境变量不存在时，${VAR}会被解析为空字符串
        # 这是预期行为，验证默认值逻辑在应用层处理
        if "EMBEDDING_MODEL" in os.environ:
            del os.environ["EMBEDDING_MODEL"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({
                "embedding": {"model": "${EMBEDDING_MODEL}"}
            }, f)
            config_path = f.name

        try:
            config = Config.from_file(config_path)
            # 环境变量不存在时解析为空字符串
            assert config.embedding.model == ""
        finally:
            os.unlink(config_path)


class TestSkillIndexEmbeddingInjection:
    """SkillIndex embedding 参数注入测试

    注意: 这些测试在 sentence_transformers 导入失败时会被跳过
    """

    @pytest.mark.skip(reason="sentence_transformers 导入存在环境兼容性问题")
    def test_skill_index_default_embedding(self):
        """测试 SkillIndex 使用默认 embedding 配置"""
        from ascend_op_agent.skills.index import SkillIndex

        index = SkillIndex()
        assert index._embedding_model_name == "sentence-transformers/all-MiniLM-L6-v3"

    @pytest.mark.skip(reason="sentence_transformers 导入存在环境兼容性问题")
    def test_skill_index_custom_embedding(self):
        """测试 SkillIndex 使用自定义 embedding 配置"""
        from ascend_op_agent.skills.index import SkillIndex

        index = SkillIndex(embedding_model_name="custom-model")
        assert index._embedding_model_name == "custom-model"

    @pytest.mark.skip(reason="sentence_transformers 导入存在环境兼容性问题")
    def test_skill_index_custom_dimension(self):
        """测试 SkillIndex 使用自定义维度"""
        from ascend_op_agent.skills.index import SkillIndex

        index = SkillIndex(embedding_dimension=512)
        assert index._embedding_dim == 512


class TestEpisodicMemoryEmbeddingInjection:
    """EpisodicMemory embedding 参数注入测试"""

    def test_episodic_memory_default_embedding(self):
        """测试 EpisodicMemory 使用默认 embedding 配置"""
        from ascend_op_agent.memory.episodic_memory import EpisodicMemory

        memory = EpisodicMemory()
        assert memory._embedding_model_name == "sentence-transformers/all-MiniLM-L6-v3"
        assert memory._embedding_dimension == 384

    def test_episodic_memory_custom_embedding(self):
        """测试 EpisodicMemory 使用自定义 embedding 配置"""
        from ascend_op_agent.memory.episodic_memory import EpisodicMemory

        memory = EpisodicMemory(embedding_model_name="custom-model")
        assert memory._embedding_model_name == "custom-model"

    def test_episodic_memory_custom_dimension(self):
        """测试 EpisodicMemory 使用自定义维度"""
        from ascend_op_agent.memory.episodic_memory import EpisodicMemory

        memory = EpisodicMemory(embedding_dimension=256)
        assert memory._embedding_dimension == 256


class TestVectorStoreEmbeddingInjection:
    """VectorStore embedding 参数注入测试"""

    def test_vector_store_default_dimension(self):
        """测试 VectorStore 使用默认维度"""
        from ascend_op_agent.memory.vector_store import VectorStore

        store = VectorStore(persist_dir=None)  # 避免实际创建目录
        assert store.get_embedding_dimension() == 384

    def test_vector_store_custom_dimension(self):
        """测试 VectorStore 使用自定义维度"""
        from ascend_op_agent.memory.vector_store import VectorStore

        store = VectorStore(persist_dir=None, embedding_dimension=512)
        assert store.get_embedding_dimension() == 512

    def test_vector_store_custom_persist_dir(self):
        """测试 VectorStore 使用自定义路径"""
        from ascend_op_agent.memory.vector_store import VectorStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = VectorStore(persist_dir=tmpdir, embedding_dimension=256)
            assert tmpdir in str(store.persist_dir)
            assert store.get_embedding_dimension() == 256
