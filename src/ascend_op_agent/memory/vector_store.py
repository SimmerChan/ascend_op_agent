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

"""VectorStore - ChromaDB 向量存储封装

提供 Skill 和记忆的向量存储与检索功能。
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings
from chromadb.api.client import Client

logger = logging.getLogger(__name__)

# ChromaDB 配置
DEFAULT_COLLECTION_SKILLS = "skills"
DEFAULT_COLLECTION_MEMORIES = "memories"


class VectorStore:
    """向量存储封装

    使用 ChromaDB 内嵌模式存储向量数据。
    """

    def __init__(
        self,
        persist_dir: Optional[str] = None,
        embedding_dimension: int = 384,
        collection_skills: str = DEFAULT_COLLECTION_SKILLS,
        collection_memories: str = DEFAULT_COLLECTION_MEMORIES,
    ):
        """
        Args:
            persist_dir: ChromaDB 持久化目录，默认从配置读取
            embedding_dimension: Embedding 向量维度，默认 384
            collection_skills: Skill 向量的 collection 名称
            collection_memories: 记忆向量的 collection 名称
        """
        if persist_dir is None:
            from ascend_op_agent.config import load_config

            cfg = load_config()
            persist_dir = cfg.vector_store.persist_dir

        self._embedding_dimension = embedding_dimension
        # 显式 expanduser —— 默认值 "~/.ascend_op_agent/vector_db" 不展开会
        # 在 CWD 下建出字面量 ~/ 目录(同 session_manager/checkpoint 模式)
        self.persist_dir = Path(persist_dir).expanduser()
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.collection_skills = collection_skills
        self.collection_memories = collection_memories

        # 初始化 ChromaDB 客户端（持久化模式）
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(
                anonymized_telemetry=False,
            ),
        )

        # 确保 collection 存在
        self._ensure_collections()

    def get_embedding_dimension(self) -> int:
        """获取 embedding 向量维度"""
        return self._embedding_dimension

    def _ensure_collections(self) -> None:
        """确保必要的 collection 存在"""
        # 获取已存在的 collection
        existing = [c.name for c in self._client.list_collections()]

        if self.collection_skills not in existing:
            self._client.create_collection(
                name=self.collection_skills, metadata={"description": "Skill 向量存储"}
            )
            logger.info(f"Created collection: {self.collection_skills}")

        if self.collection_memories not in existing:
            self._client.create_collection(
                name=self.collection_memories, metadata={"description": "记忆向量存储"}
            )
            logger.info(f"Created collection: {self.collection_memories}")

    def get_collection(self, name: str):
        """获取指定名称的 collection

        Args:
            name: collection 名称

        Returns:
            ChromaDB Collection 对象
        """
        return self._client.get_collection(name=name)

    def get_skills_collection(self):
        """获取 Skill 向量 collection"""
        return self.get_collection(self.collection_skills)

    def get_memories_collection(self):
        """获取记忆向量 collection"""
        return self.get_collection(self.collection_memories)

    def add_skill_vector(
        self,
        skill_id: str,
        embedding: list[float],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """添加 Skill 向量

        Args:
            skill_id: Skill 唯一标识
            embedding: 向量 embedding
            metadata: 元数据（name, description, tags 等）
        """
        collection = self.get_skills_collection()

        collection.add(
            ids=[skill_id],
            embeddings=[embedding],
            metadatas=[metadata] if metadata else None,
        )
        logger.debug(f"Added skill vector: {skill_id}")

    def add_skill_vectors(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """批量添加 Skill 向量

        Args:
            ids: Skill 唯一标识列表
            embeddings: 向量 embedding 列表
            metadatas: 元数据列表
        """
        collection = self.get_skills_collection()

        collection.add(
            ids=ids,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.debug(f"Added {len(ids)} skill vectors")

    def search_skill_vectors(
        self,
        query_embedding: list[float],
        k: int = 5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """搜索 Skill 向量

        Args:
            query_embedding: 查询向量
            k: 返回数量
            filter_metadata: 元数据过滤条件

        Returns:
            匹配的 Skill 列表，包含 id、distance、metadata
        """
        collection = self.get_skills_collection()

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=filter_metadata,
        )

        # 格式化结果
        formatted = []
        if results and results["ids"]:
            for i, sid in enumerate(results["ids"][0]):
                formatted.append(
                    {
                        "id": sid,
                        "distance": results["distances"][0][i] if "distances" in results else None,
                        "metadata": (
                            results["metadatas"][0][i]
                            if "metadatas" in results and results["metadatas"]
                            else None
                        ),
                    }
                )

        return formatted

    def delete_skill_vector(self, skill_id: str) -> None:
        """删除 Skill 向量

        Args:
            skill_id: Skill 唯一标识
        """
        collection = self.get_skills_collection()
        collection.delete(ids=[skill_id])
        logger.debug(f"Deleted skill vector: {skill_id}")

    def update_skill_vector(
        self,
        skill_id: str,
        embedding: list[float],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """更新 Skill 向量

        Args:
            skill_id: Skill 唯一标识
            embedding: 新向量 embedding
            metadata: 新元数据
        """
        collection = self.get_skills_collection()

        collection.update(
            ids=[skill_id],
            embeddings=[embedding],
            metadatas=[metadata] if metadata else None,
        )
        logger.debug(f"Updated skill vector: {skill_id}")

    def add_memory_vector(
        self,
        memory_id: str,
        embedding: list[float],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """添加记忆向量

        Args:
            memory_id: 记忆唯一标识
            embedding: 向量 embedding
            metadata: 元数据
        """
        collection = self.get_memories_collection()

        collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            metadatas=[metadata] if metadata else None,
        )
        logger.debug(f"Added memory vector: {memory_id}")

    def search_memory_vectors(
        self,
        query_embedding: list[float],
        k: int = 5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """搜索记忆向量

        Args:
            query_embedding: 查询向量
            k: 返回数量
            filter_metadata: 元数据过滤条件

        Returns:
            匹配的記憶列表
        """
        collection = self.get_memories_collection()

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=filter_metadata,
        )

        # 格式化结果
        formatted = []
        if results and results["ids"]:
            for i, mid in enumerate(results["ids"][0]):
                formatted.append(
                    {
                        "id": mid,
                        "distance": results["distances"][0][i] if "distances" in results else None,
                        "metadata": (
                            results["metadatas"][0][i]
                            if "metadatas" in results and results["metadatas"]
                            else None
                        ),
                    }
                )

        return formatted

    def get_skill_count(self) -> int:
        """获取 Skill 向量数量"""
        collection = self.get_skills_collection()
        return collection.count()

    def get_memory_count(self) -> int:
        """获取记忆向量数量"""
        collection = self.get_memories_collection()
        return collection.count()

    def clear_skills(self) -> None:
        """清空所有 Skill 向量"""
        self._client.delete_collection(name=self.collection_skills)
        self._client.create_collection(
            name=self.collection_skills, metadata={"description": "Skill 向量存储"}
        )
        logger.info("Cleared all skill vectors")

    def clear_memories(self) -> None:
        """清空所有记忆向量"""
        self._client.delete_collection(name=self.collection_memories)
        self._client.create_collection(
            name=self.collection_memories, metadata={"description": "记忆向量存储"}
        )
        logger.info("Cleared all memory vectors")

    def reset(self) -> None:
        """重置所有数据"""
        self.clear_skills()
        self.clear_memories()
        logger.info("Reset all vector data")
