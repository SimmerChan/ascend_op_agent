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

"""SkillIndex - Skill索引和检索

参考Hermes Agent实现的两层缓存机制:
- Layer 1: 进程内LRU缓存
- Layer 2: 磁盘快照

使用SQLite FTS5进行全文搜索。
"""

import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from sentence_transformers import SentenceTransformer

from ascend_op_agent.skills.models import Skill, SkillInfo
from ascend_op_agent.skills.repository import SkillRepository

logger = logging.getLogger(__name__)

# 最大LRU缓存条目数（参考Hermes的8条）
MAX_LRU_CACHE = 8

# 快照版本
SNAPSHOT_VERSION = 1

# 快照文件名
SNAPSHOT_FILENAME = ".skills_prompt_snapshot.json"

# Embedding 模型配置
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v3"
EMBEDDING_DIM = 384


class SkillIndex:
    """Skill索引和检索

    支持:
    - SQLite FTS5全文搜索
    - 两层缓存（LRU + 磁盘快照）
    - 混合检索（向量 + 关键词）
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        cache_dir: Optional[str] = None,
        vector_store_dir: Optional[str] = None,
    ):
        """
        Args:
            db_path: SQLite数据库路径
            cache_dir: 缓存目录（用于存储快照）
            vector_store_dir: VectorStore持久化目录
        """
        self.cache_dir = Path(
            cache_dir or os.path.expanduser("~/.ascend_op_agent")
        )
        self.db_path = db_path or str(self.cache_dir / "skills_index.db")

        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 初始化数据库
        self._init_db()

        # 加载磁盘快照
        self._load_snapshot()

        # LRU缓存
        self._lru_cache: OrderedDict[str, list[Skill]] = OrderedDict()
        self._cache_lock = threading.Lock()

        # 初始化向量存储
        from ascend_op_agent.memory.vector_store import VectorStore
        self._vector_store = VectorStore(persist_dir=vector_store_dir)

        # 延迟初始化embedding模型（避免测试环境网络问题）
        # 如果模型加载失败，向量功能将被禁用但FTS5功能保留
        self._embedding_model = None
        self._embedding_model_name = EMBEDDING_MODEL

    @property
    def embedding_model(self) -> Optional[SentenceTransformer]:
        """惰性加载embedding模型"""
        if self._embedding_model is None:
            try:
                self._embedding_model = SentenceTransformer(self._embedding_model_name)
            except Exception as e:
                logger.warning(f"Failed to load embedding model: {e}")
                return None
        return self._embedding_model

    def _init_db(self) -> None:
        """初始化SQLite数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 创建FTS5虚拟表
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skills USING fts5(
                name,
                description,
                tags,
                content,
                tokenize='porter unicode61'
            )
        """)

        conn.commit()
        conn.close()

    def add_skill(self, skill: Skill) -> None:
        """添加Skill到索引

        Args:
            skill: Skill对象
        """
        # 先写入SQLite FTS5（这是主要索引）
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 删除已存在的同名skill
        cursor.execute("DELETE FROM skills WHERE name = ?", (skill.name,))

        # 插入新skill
        cursor.execute(
            """
            INSERT INTO skills (name, description, tags, content)
            VALUES (?, ?, ?, ?)
            """,
            (
                skill.name,
                skill.description,
                ",".join(skill.tags),
                skill.content,
            ),
        )

        conn.commit()
        conn.close()

        # 尝试写入ChromaDB向量（失败不影响主流程）
        try:
            if self.embedding_model is not None:
                embedding = self.embedding_model.encode(skill.content).tolist()
                vector_id = f"skill_{skill.name}"
                metadata = {
                    "name": skill.name,
                    "description": skill.description,
                    "tags": ",".join(skill.tags) if skill.tags else "",
                }
                self._vector_store.add_skill_vector(vector_id, embedding, metadata)
        except Exception as e:
            logger.warning(f"Failed to add skill vector to ChromaDB: {e}")

        # 清除LRU缓存
        self._clear_lru_cache()

        # 更新磁盘快照
        self._save_snapshot()

    def remove_skill(self, name: str) -> None:
        """从索引移除Skill

        Args:
            name: Skill名称
        """
        # 从ChromaDB删除向量
        vector_id = f"skill_{name}"
        try:
            self._vector_store.delete_skill_vector(vector_id)
        except Exception as e:
            logger.warning(f"Failed to delete skill vector from ChromaDB: {e}")

        # 从SQLite FTS5删除
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM skills WHERE name = ?", (name,))

        conn.commit()
        conn.close()

        # 清除LRU缓存
        self._clear_lru_cache()

        # 更新磁盘快照
        self._save_snapshot()

    def search(self, query: str, k: int = 5) -> list[Skill]:
        """搜索Skill

        Args:
            query: 搜索query
            k: 返回数量

        Returns:
            匹配的Skill列表
        """
        cache_key = f"search:{query}:{k}"

        # 检查LRU缓存
        with self._cache_lock:
            if cache_key in self._lru_cache:
                # 移到末尾（最近使用）
                self._lru_cache.move_to_end(cache_key)
                return self._lru_cache[cache_key]

        # 执行搜索
        results = self._do_search(query, k)

        # 更新LRU缓存
        with self._cache_lock:
            # 如果缓存已满，移除最旧的条目
            if len(self._lru_cache) >= MAX_LRU_CACHE:
                self._lru_cache.popitem(last=False)

            self._lru_cache[cache_key] = results

        return results

    def _do_search(self, query: str, k: int = 5) -> list[Skill]:
        """实际执行搜索

        Args:
            query: 搜索query
            k: 返回数量

        Returns:
            匹配的Skill列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 使用FTS5的bm25排序
            cursor.execute(
                """
                SELECT name, description, tags, content,
                       bm25(skills) as score
                FROM skills
                WHERE skills MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (query, k),
            )

            rows = cursor.fetchall()

            skills = []
            for row in rows:
                skills.append(
                    Skill(
                        name=row[0],
                        description=row[1],
                        content=row[3],
                        tags=row[2].split(",") if row[2] else [],
                    )
                )

            return skills

        except Exception as e:
            logger.warning(f"FTS search failed, falling back to LIKE: {e}")
            # FTS失败时回退到LIKE搜索
            cursor.execute(
                """
                SELECT name, description, tags, content
                FROM skills
                WHERE name LIKE ? OR description LIKE ? OR tags LIKE ?
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%", k),
            )

            rows = cursor.fetchall()

            skills = []
            for row in rows:
                skills.append(
                    Skill(
                        name=row[0],
                        description=row[1],
                        content=row[3],
                        tags=row[2].split(",") if row[2] else [],
                    )
                )

            return skills

        finally:
            conn.close()

    def search_by_vector(
        self,
        query_embedding: list[float],
        k: int = 5,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[Skill]:
        """基于向量相似度搜索Skill

        Args:
            query_embedding: 查询向量
            k: 返回数量
            filter_metadata: 元数据过滤条件

        Returns:
            匹配的Skill列表，按相似度排序
        """
        results = self._vector_store.search_skill_vectors(
            query_embedding=query_embedding,
            k=k,
            filter_metadata=filter_metadata,
        )

        skills = []
        for result in results:
            # 从SQLite FTS5获取完整Skill信息
            skill = self._get_skill_by_name(result["id"].replace("skill_", ""))
            if skill:
                skills.append(skill)

        return skills

    def _get_skill_by_name(self, name: str) -> Optional[Skill]:
        """根据名称从FTS5获取Skill完整信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT name, description, tags, content FROM skills WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()

            if row:
                return Skill(
                    name=row[0],
                    description=row[1],
                    content=row[3],
                    tags=row[2].split(",") if row[2] else [],
                )
            return None
        finally:
            conn.close()

    def hybrid_search(
        self,
        query: str,
        k: int = 5,
        alpha: float = 0.4,
    ) -> list[Skill]:
        """混合检索：FTS5 + 向量

        Args:
            query: 搜索query
            k: 返回数量
            alpha: FTS5权重 (0-1)，向量权重为 (1-alpha)

        Returns:
            混合排序后的Skill列表
        """
        # FTS5搜索
        fts_results = self._do_search(query, k * 2)

        # 如果embedding模型不可用，回退到纯FTS5
        if self.embedding_model is None:
            return fts_results[:k]

        # 向量搜索
        try:
            query_embedding = self.embedding_model.encode(query).tolist()
            vector_results = self.search_by_vector(query_embedding, k * 2)
        except Exception as e:
            logger.warning(f"Vector search failed, falling back to FTS5: {e}")
            return fts_results[:k]

        # 融合排序
        fts_scores = {s.name: 1.0 / (i + 1) for i, s in enumerate(fts_results)}
        vector_scores = {s.name: 1.0 / (i + 1) for i, s in enumerate(vector_results)}

        all_skills = {s.name: s for s in fts_results + vector_results}

        fused_scores = []
        for name, skill in all_skills.items():
            fts_s = fts_scores.get(name, 0)
            vec_s = vector_scores.get(name, 0)
            score = alpha * fts_s + (1 - alpha) * vec_s
            fused_scores.append((score, name, skill))

        fused_scores.sort(key=lambda x: x[0], reverse=True)

        return [s[2] for s in fused_scores[:k]]

    @lru_cache(maxsize=MAX_LRU_CACHE)
    def search_cached(self, query: str, k: int = 5) -> tuple[str, ...]:
        """带LRU缓存的搜索（用于decorator缓存）

        Args:
            query: 搜索query
            k: 返回数量

        Returns:
            结果的字符串形式（用于缓存）
        """
        results = self._do_search(query, k)
        return tuple(s.name for s in results)

    def _clear_lru_cache(self) -> None:
        """清除LRU缓存"""
        with self._cache_lock:
            self._lru_cache.clear()
        # 清除装饰器缓存
        self.search_cached.cache_clear()

    def rebuild_index(self, repository: SkillRepository) -> None:
        """重建索引

        Args:
            repository: SkillRepository实例
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 清空现有索引
        cursor.execute("DELETE FROM skills")

        # 从repository加载所有skill
        for skills_dir in repository.get_all_skills_dirs():
            for skill_index in repository.iter_skill_index_files(skills_dir):
                skill = repository.load_skill(skill_index)
                if skill:
                    cursor.execute(
                        """
                        INSERT INTO skills (name, description, tags, content)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            skill.name,
                            skill.description,
                            ",".join(skill.tags),
                            skill.content,
                        ),
                    )

        conn.commit()
        conn.close()

        # 清除缓存并保存快照
        self._clear_lru_cache()
        self._save_snapshot()

        logger.info("Skill index rebuilt successfully")

    # ==================== 磁盘快照相关 ====================

    def _snapshot_path(self) -> Path:
        """获取快照文件路径"""
        return self.cache_dir / SNAPSHOT_FILENAME

    def _load_snapshot(self) -> None:
        """加载磁盘快照"""
        snapshot_path = self._snapshot_path()
        if not snapshot_path.exists():
            return

        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)

            # 验证版本
            if snapshot.get("version") != SNAPSHOT_VERSION:
                logger.warning("Snapshot version mismatch, ignoring")
                return

            logger.info(f"Loaded skill snapshot from {snapshot_path}")

        except Exception as e:
            logger.warning(f"Failed to load snapshot: {e}")

    def _save_snapshot(self) -> None:
        """保存磁盘快照"""
        try:
            # 构建快照数据
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT name, description, tags FROM skills")
            rows = cursor.fetchall()

            skill_entries = [
                {"name": row[0], "description": row[1], "tags": row[2]}
                for row in rows
            ]

            conn.close()

            snapshot = {
                "version": SNAPSHOT_VERSION,
                "timestamp": time.time(),
                "skills": skill_entries,
            }

            # 原子写入
            snapshot_path = self._snapshot_path()
            temp_path = snapshot_path.with_suffix(".tmp")

            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f)

            temp_path.replace(snapshot_path)

            logger.debug(f"Saved skill snapshot to {snapshot_path}")

        except Exception as e:
            logger.warning(f"Failed to save snapshot: {e}")

    def clear_snapshot(self) -> None:
        """清除磁盘快照"""
        snapshot_path = self._snapshot_path()
        if snapshot_path.exists():
            snapshot_path.unlink()
            logger.info("Cleared skill snapshot")

    def clear_all_caches(self) -> None:
        """清除所有缓存（内存 + 磁盘快照）"""
        self._clear_lru_cache()
        self.clear_snapshot()
