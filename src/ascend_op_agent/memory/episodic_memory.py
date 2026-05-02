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

"""EpisodicMemory - 会话历史记忆

完整存储会话历史到 ChromaDB，支持:
- 完整会话记录
- 可选的会话摘要
- 相似会话检索
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ascend_op_agent.memory.vector_store import VectorStore

logger = logging.getLogger(__name__)

# 默认会话消息数阈值
DEFAULT_MESSAGE_THRESHOLD = 50

# 默认token阈值
DEFAULT_TOKEN_THRESHOLD = 4000


@dataclass
class ConversationTurn:
    """对话轮次"""
    role: str  # user, assistant, system
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class Episode:
    """会话片段"""
    episode_id: str
    turns: list[ConversationTurn]
    summary: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def message_count(self) -> int:
        """获取消息数量"""
        return len(self.turns)

    @property
    def total_tokens(self) -> int:
        """估算token数（简单按字符数/4估算）"""
        return sum(len(turn.content) for turn in self.turns) // 4


class EpisodicMemory:
    """会话历史记忆

    将完整会话存储到 ChromaDB，支持相似会话检索。
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        message_threshold: int = DEFAULT_MESSAGE_THRESHOLD,
        token_threshold: int = DEFAULT_TOKEN_THRESHOLD,
        summarize_episodes: bool = False,
        embedding_model_name: Optional[str] = None,
        embedding_dimension: int = 384,
    ):
        """
        Args:
            vector_store: VectorStore实例
            message_threshold: 触发摘要的消息数阈值
            token_threshold: 触发摘要的token阈值
            summarize_episodes: 是否启用摘要提取
            embedding_model_name: embedding模型名称
            embedding_dimension: embedding向量维度
        """
        from ascend_op_agent.config import load_config
        cfg = load_config()

        self._vector_store = vector_store or VectorStore()
        self._message_threshold = message_threshold
        self._token_threshold = token_threshold
        self._summarize_episodes = summarize_episodes

        self._embedding_model_name = embedding_model_name or cfg.embedding.model
        self._embedding_dimension = embedding_dimension
        self._embedding_model = None  # 惰性加载

        # 当前会话
        self._current_episode: Optional[Episode] = None
        self._episode_counter = 0

    @property
    def _model(self):
        """惰性加载embedding模型"""
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedding_model = SentenceTransformer(self._embedding_model_name)
            except Exception as e:
                logger.warning(f"Failed to load embedding model: {e}")
                return None
        return self._embedding_model

    def start_episode(self, episode_id: Optional[str] = None) -> str:
        """开始新会话片段

        Args:
            episode_id: 会话片段ID，默认自动生成

        Returns:
            生成的会话片段ID
        """
        if episode_id is None:
            self._episode_counter += 1
            episode_id = f"episode_{int(time.time())}_{self._episode_counter}"

        self._current_episode = Episode(
            episode_id=episode_id,
            turns=[],
            metadata={"created_at": time.time()},
        )

        logger.info(f"Started new episode: {episode_id}")
        return episode_id

    def add_turn(self, role: str, content: str) -> None:
        """添加对话轮次

        Args:
            role: 角色 (user, assistant, system)
            content: 内容
        """
        if self._current_episode is None:
            self.start_episode()

        turn = ConversationTurn(role=role, content=content)
        self._current_episode.turns.append(turn)

        # 检查是否需要摘要
        if self._should_summarize():
            self._maybe_summarize()

    def _should_summarize(self) -> bool:
        """检查是否应该生成摘要"""
        if not self._summarize_episodes:
            return False

        if self._current_episode is None:
            return False

        return (
            self._current_episode.message_count >= self._message_threshold
            or self._current_episode.total_tokens >= self._token_threshold
        )

    def _maybe_summarize(self) -> None:
        """生成摘要（如果需要且启用）"""
        if self._current_episode is None:
            return

        # 简单的摘要生成（提取首尾消息的关键词）
        # 完整实现需要LLM，这里使用简单的启发式方法
        first_turn = self._current_episode.turns[0] if self._current_episode.turns else None
        last_turn = self._current_episode.turns[-1] if self._current_episode.turns else None

        summary_parts = ["[会话摘要]"]
        if first_turn:
            summary_parts.append(f"开始于: {first_turn.content[:100]}...")
        if last_turn and last_turn != first_turn:
            summary_parts.append(f"结束于: {last_turn.content[:100]}...")
        summary_parts.append(f"共{self._current_episode.message_count}条消息")

        self._current_episode.summary = "\n".join(summary_parts)
        logger.info(f"Generated summary for episode {self._current_episode.episode_id}")

    def end_episode(self) -> Optional[Episode]:
        """结束当前会话片段

        Returns:
            结束的会话片段
        """
        if self._current_episode is None:
            return None

        episode = self._current_episode

        # 存储到向量数据库
        self._store_episode(episode)

        self._current_episode = None
        logger.info(f"Ended episode: {episode.episode_id}")

        return episode

    def _store_episode(self, episode: Episode) -> None:
        """将会话片段存储到向量数据库

        Args:
            episode: 会话片段
        """
        # 生成内容表示
        content = self._episode_to_content(episode)

        # 生成向量
        model = self._model
        if model is not None:
            try:
                embedding = model.encode(content).tolist()
            except Exception as e:
                logger.warning(f"Failed to encode episode: {e}")
                embedding = [0.0] * self._embedding_dimension
        else:
            embedding = [0.0] * self._embedding_dimension

        # 存储到ChromaDB
        metadata = {
            "episode_id": episode.episode_id,
            "message_count": episode.message_count,
            "summary": episode.summary or "",
            "created_at": episode.metadata.get("created_at", time.time()),
        }

        self._vector_store.add_memory_vector(
            memory_id=episode.episode_id,
            embedding=embedding,
            metadata=metadata,
        )

    def _episode_to_content(self, episode: Episode) -> str:
        """将会话片段转换为文本内容

        Args:
            episode: 会话片段

        Returns:
            文本内容
        """
        parts = [f"## {episode.episode_id}"]
        parts.append(f"摘要: {episode.summary or '无'}")

        for turn in episode.turns:
            parts.append(f"\n[{turn.role}]: {turn.content}")

        return "\n".join(parts)

    def search_similar_episodes(
        self,
        query: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """搜索相似会话

        Args:
            query: 查询query
            k: 返回数量

        Returns:
            相似会话列表
        """
        model = self._model
        if model is None:
            return []

        try:
            query_embedding = model.encode(query).tolist()
        except Exception as e:
            logger.warning(f"Failed to encode query: {e}")
            return []

        return self._vector_store.search_memory_vectors(
            query_embedding=query_embedding,
            k=k,
        )

    def get_episode(self, episode_id: str) -> Optional[Episode]:
        """获取会话片段

        Args:
            episode_id: 会话片段ID

        Returns:
            会话片段或None
        """
        # 从向量数据库获取
        results = self._vector_store.search_memory_vectors(
            query_embedding=[0.0] * self._embedding_dimension,  # 零向量
            k=100,
        )

        for result in results:
            if result["id"] == episode_id:
                return self._result_to_episode(result)

        return None

    def _result_to_episode(self, result: dict[str, Any]) -> Episode:
        """将搜索结果转换为Episode对象"""
        metadata = result.get("metadata", {})
        return Episode(
            episode_id=result["id"],
            turns=[],  # 简化版不保留完整turns
            summary=metadata.get("summary"),
            metadata=metadata,
        )

    def get_current_episode(self) -> Optional[Episode]:
        """获取当前会话片段"""
        return self._current_episode

    def list_episodes(self, limit: int = 100) -> list[dict[str, Any]]:
        """列出所有会话片段

        Args:
            limit: 返回数量

        Returns:
            会话片段信息列表
        """
        results = self._vector_store.search_memory_vectors(
            query_embedding=[0.0] * self._embedding_dimension,
            k=limit,
        )

        return [
            {
                "episode_id": r["id"],
                "metadata": r.get("metadata", {}),
                "distance": r.get("distance"),
            }
            for r in results
        ]
