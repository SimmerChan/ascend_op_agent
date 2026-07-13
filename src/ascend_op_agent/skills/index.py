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
        embedding_model_name: Optional[str] = None,
        embedding_dimension: int = 384,
    ):
        """
        Args:
            db_path: SQLite数据库路径
            cache_dir: 缓存目录（用于存储快照）
            vector_store_dir: VectorStore持久化目录
            embedding_model_name: Embedding模型名称
            embedding_dimension: Embedding向量维度
        """
        from ascend_op_agent.config import load_config

        cfg = load_config()
        self._embedding_model_name = embedding_model_name or cfg.embedding.model
        self._embedding_dim = embedding_dimension

        self.cache_dir = Path(cache_dir or os.path.expanduser("~/.ascend_op_agent"))
        self.db_path = db_path or str(self.cache_dir / "skills_index.db")

        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # PR-B: v1→v2 migration(在 _init_db 之前)—— 4 列→6 列 + crash-safe
        self._maybe_migrate_to_v2()

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
        """初始化SQLite数据库(PR-B v2 schema: task_type + topic 列 + schema_meta 表)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # PR-B: 检测 schema_version 并处理 crash restore
        # 若 .skills_index.bak.json 存在且 schema_meta 不一致 → 启动时自动 restore
        self._maybe_restore_from_disk_backup(conn)

        # 创建FTS5虚拟表(PR-B 加 task_type + topic 两列)
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS skills USING fts5(
                name,
                description,
                tags,
                content,
                task_type,
                topic,
                tokenize='porter unicode61'
            )
        """
        )

        # PR-B: schema_meta 表存 schema_version(独立于 FTS5 virtual table)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """
        )
        # 写入当前 schema_version(INSERT OR IGNORE 兼容既有)
        cursor.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', '2')"
        )

        conn.commit()
        conn.close()

    def _get_schema_version(self) -> Optional[str]:
        """读 schema_meta.schema_version; 不存在则返回 None(legacy v1 DB)."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.OperationalError:
            return None

    def _migration_backup_path(self) -> Path:
        """disk 备份文件路径(PR-B: persist backup to disk before DROP)"""
        return self.cache_dir / ".skills_index.bak.json"

    def _maybe_restore_from_disk_backup(self, conn: sqlite3.Connection) -> None:
        """启动时检测: 若 .skills_index.bak.json 存在但 schema_version 未升,
        说明上次 migration 在 DROP 后 reinsert 前 crash → restore from disk.
        """
        backup = self._migration_backup_path()
        if not backup.exists():
            return
        # 读 backup metadata
        try:
            with open(backup, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read migration backup {backup}: {e}")
            return
        # 检测: backup.migrating=True 但 schema_version 未升 = 崩溃中
        if not data.get("migrating"):
            return
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
        row = cursor.fetchone()
        current = row[0] if row else None
        if current == "2":
            # 已升级,清理 backup
            try:
                backup.unlink()
            except OSError:
                pass
            return
        # Restore: 用 backup 中的 rows 重建 skills 表
        logger.warning(f"Detected interrupted migration, restoring from {backup}")
        cursor.execute("DELETE FROM skills")
        for r in data.get("rows", []):
            cursor.execute(
                """INSERT INTO skills (name, description, tags, content, task_type, topic)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    r["name"],
                    r["description"],
                    r.get("tags", ""),
                    r.get("content", ""),
                    r.get("task_type", ""),
                    r.get("topic", ""),
                ),
            )
        cursor.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '2')"
        )
        conn.commit()
        try:
            backup.unlink()
            logger.info("Migration restore complete, backup removed")
        except OSError:
            pass

    def _maybe_migrate_to_v2(self) -> None:
        """PR-B v1→v2 migration. Idempotent: skip on v2 / fresh DB; run on v1.

        顺序: 此函数在 _init_db 之前运行 → skills / schema_meta 表可能不存在。
        需容错处理 fresh DB(无表)、v1 DB(4 列 + 无 schema_meta)、v2 DB(6 列 + schema_version='2')。
        """
        if not Path(self.db_path).exists():
            return  # 完全 fresh DB → _init_db 会创建 v2
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # 优先检测 crash-state: 若 disk backup 存在且标记 migrating,
            # 说明上次 migration 在 DROP 后 reinsert 前 crash → 先 restore
            self._maybe_restore_from_disk_backup(conn)
            # 1. schema_meta 存在? 决定 v1 vs v2
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
            )
            has_meta = cursor.fetchone() is not None
            if has_meta:
                cursor.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'")
                row = cursor.fetchone()
                if row and row[0] == "2":
                    return  # v2 已 ship
            # 2. skills 表存在 + 列数 = 4 → v1, 走 migration
            # 3. skills 表不存在 → fresh DB, _init_db 接下来会创建 v2
            # 4. skills 表存在 + 列数 = 6(防御:已有 v2 列但缺 schema_meta)→ 写 schema_meta
            cursor.execute("PRAGMA table_info(skills)")
            cols = [r[1] for r in cursor.fetchall()]
            if not cols:
                return  # fresh DB
            if "task_type" in cols and "topic" in cols:
                # 防御: v2 列存在但 schema_meta 缺 → 补 schema_meta
                cursor.execute(
                    "INSERT OR IGNORE INTO schema_meta (key, value) "
                    "VALUES ('schema_version', '2')"
                )
                conn.commit()
                return
            # v1 schema → migration
            self._do_migration_v1_to_v2(conn, cursor)
        finally:
            conn.close()

    def _do_migration_v1_to_v2(self, conn: sqlite3.Connection, cursor) -> None:
        # 2. backup all rows to disk
        cursor.execute("SELECT name, description, tags, content FROM skills")
        rows = cursor.fetchall()
        backup = self._migration_backup_path()
        backup_data = {
            "migrating": True,
            "timestamp": time.time(),
            "rows": [
                {
                    "name": r[0],
                    "description": r[1],
                    "tags": r[2],
                    "content": r[3],
                    "task_type": "",
                    "topic": "",
                }
                for r in rows
            ],
        }
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False)
        # 3. DROP + CREATE 含 6 列
        cursor.execute("DROP TABLE IF EXISTS skills")
        cursor.execute(
            """
            CREATE VIRTUAL TABLE skills USING fts5(
                name, description, tags, content, task_type, topic,
                tokenize='porter unicode61'
            )
        """
        )
        # 4. reinsert (task_type/topic 暂 NULL,迁移期 NULL → 走 name-only fallback)
        for r in rows:
            cursor.execute(
                """INSERT INTO skills (name, description, tags, content, task_type, topic)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (r[0], r[1], r[2], r[3], "", ""),
            )
        # 5. 创建 schema_meta 表 + 写 schema_version + commit
        # (此函数在 _init_db 之前运行,schema_meta 还不存在,需在此创建)
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """
        )
        cursor.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', '2')"
        )
        conn.commit()
        conn.close()
        # 6. 成功后清理 backup
        try:
            backup.unlink()
            logger.info(f"SkillsIndex v1→v2 migration complete: {len(rows)} rows")
        except OSError:
            pass

    def add_skill(
        self,
        skill: Skill,
        task_type: str = "",
        topic: str = "",
    ) -> None:
        """添加Skill到索引(PR-B: 新增 task_type/topic keyword-only 参数)

        task_type/topic 默认 ""(走 name-only filter); PR-A 旧代码 add_skill(skill) 仍兼容
        (task_type="" topic="" 时 FTS5 WHERE clause 不过滤)。

        Args:
            skill: Skill对象
            task_type: 任务类型(migrate/analyze/optimize/develop,空字符串=不索引)
            topic: free-form topic 标签(空字符串=不索引)
        """
        # 先写入SQLite FTS5（这是主要索引）
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 删除已存在的同名skill
        cursor.execute("DELETE FROM skills WHERE name = ?", (skill.name,))

        # 插入新skill(PR-B 6 列)
        cursor.execute(
            """
            INSERT INTO skills (name, description, tags, content, task_type, topic)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                skill.name,
                skill.description,
                ",".join(skill.tags),
                skill.content,
                task_type,
                topic,
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

    def _do_search(
        self,
        query: str,
        k: int = 5,
        task_type: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> list[Skill]:
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
            # PR-B: FTS5 查询 + task_type/topic 过滤(过滤参数为空则不加 WHERE 条件)
            where_clauses = ["skills MATCH ?"]
            params: list[Any] = [query]
            if task_type:
                where_clauses.append("task_type = ?")
                params.append(task_type)
            if topic:
                where_clauses.append("topic = ?")
                params.append(topic)
            params.append(k)
            sql = f"""
                SELECT name, description, tags, content, task_type, topic,
                       bm25(skills) as score
                FROM skills
                WHERE {' AND '.join(where_clauses)}
                ORDER BY score
                LIMIT ?
            """
            cursor.execute(sql, params)

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
            like_clauses = ["(name LIKE ? OR description LIKE ? OR tags LIKE ?)"]
            like_params: list[Any] = [f"%{query}%", f"%{query}%", f"%{query}%"]
            if task_type:
                like_clauses.append("task_type = ?")
                like_params.append(task_type)
            if topic:
                like_clauses.append("topic = ?")
                like_params.append(topic)
            like_params.append(k)
            cursor.execute(
                f"""
                SELECT name, description, tags, content, task_type, topic
                FROM skills
                WHERE {' AND '.join(like_clauses)}
                LIMIT ?
                """,
                like_params,
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

    def search_by_task_type(self, task_type: str) -> list[Skill]:
        """PR-B U2/R5b helper: 按 task_type 过滤的便捷方法(读 FTS5 全部匹配按名字序)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, description, tags, content, task_type, topic "
            "FROM skills WHERE task_type = ? ORDER BY name",
            (task_type,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            Skill(
                name=r[0],
                description=r[1],
                content=r[3],
                tags=r[2].split(",") if r[2] else [],
            )
            for r in rows
        ]

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
        task_type: Optional[str] = None,
        topic: Optional[str] = None,
    ) -> list[Skill]:
        """混合检索：FTS5 + 向量(PR-B: 加 task_type/topic 过滤)

        Args:
            query: 搜索query
            k: 返回数量
            alpha: FTS5权重 (0-1)，向量权重为 (1-alpha)
            task_type: 可选 task_type 过滤(PR-B)
            topic: 可选 topic 过滤(PR-B)

        Returns:
            混合排序后的Skill列表
        """
        # FTS5搜索(PR-B: 透传 task_type/topic)
        fts_results = self._do_search(query, k * 2, task_type=task_type, topic=topic)

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
                {"name": row[0], "description": row[1], "tags": row[2]} for row in rows
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
