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

"""MemorySystem - 统一记忆接口

整合四层记忆:
- WorkingMemory: 当前会话上下文
- EpisodicMemory: 完整会话历史
- SemanticMemory: Skill 知识向量
- ProceduralMemory: 场景化工作流模板
"""

import logging
from typing import Any, Optional

from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.memory.episodic_memory import EpisodicMemory
from ascend_op_agent.memory.semantic_memory import SemanticMemory

logger = logging.getLogger(__name__)


class MemorySystem:
    """统一记忆系统

    整合四层记忆，提供统一的检索接口。
    """

    def __init__(
        self,
        working_memory: Optional[MemoryStore] = None,
        episodic_memory: Optional[EpisodicMemory] = None,
        semantic_memory: Optional[SemanticMemory] = None,
    ):
        """
        Args:
            working_memory: WorkingMemory实例（当前会话上下文）
            episodic_memory: EpisodicMemory实例（会话历史）
            semantic_memory: SemanticMemory实例（技能知识）
        """
        self._working_memory = working_memory or MemoryStore()
        self._episodic_memory = episodic_memory or EpisodicMemory()
        self._semantic_memory = semantic_memory or SemanticMemory()

    # ==================== Working Memory ====================

    def add_working_memory(self, pool: str, content: str) -> None:
        """添加工作记忆

        Args:
            pool: 记忆池名称 (memory 或 user)
            content: 记忆内容
        """
        self._working_memory.add(pool, content)

    def get_working_memory(self, pool: str) -> list[str]:
        """获取工作记忆

        Args:
            pool: 记忆池名称

        Returns:
            记忆列表
        """
        return self._working_memory.get(pool)

    def format_working_memory(self, pool: str) -> str:
        """格式化工作记忆用于系统Prompt

        Args:
            pool: 记忆池名称

        Returns:
            格式化后的记忆内容
        """
        return self._working_memory.format_for_system_prompt(pool)

    # ==================== Episodic Memory ====================

    def start_episode(self, episode_id: Optional[str] = None) -> str:
        """开始新会话片段

        Args:
            episode_id: 会话片段ID

        Returns:
            生成的会话片段ID
        """
        return self._episodic_memory.start_episode(episode_id)

    def add_episode_turn(self, role: str, content: str) -> None:
        """添加对话轮次到当前会话

        Args:
            role: 角色 (user, assistant, system)
            content: 内容
        """
        self._episodic_memory.add_turn(role, content)

    def end_episode(self) -> Optional[Any]:
        """结束当前会话片段

        Returns:
            结束的会话片段
        """
        return self._episodic_memory.end_episode()

    def search_episodes(self, query: str, k: int = 5) -> list[dict]:
        """搜索相似会话

        Args:
            query: 查询query
            k: 返回数量

        Returns:
            相似会话列表
        """
        return self._episodic_memory.search_similar_episodes(query, k)

    # ==================== Semantic Memory ====================

    def search_skills(
        self,
        query: str,
        k: int = 5,
        op_type: Optional[str] = None,
        use_hybrid: bool = True,
    ) -> list[Any]:
        """搜索相关Skill

        Args:
            query: 搜索query
            k: 返回数量
            op_type: 算子类型过滤
            use_hybrid: 是否使用混合检索

        Returns:
            匹配的Skill列表
        """
        return self._semantic_memory.semantic_search(
            query=query,
            k=k,
            op_type=op_type,
            use_hybrid=use_hybrid,
        )

    def get_skill_recommendations(
        self,
        current_op_type: str,
        current_task: str,
        k: int = 3,
    ) -> list[dict]:
        """获取Skill推荐

        Args:
            current_op_type: 当前算子类型
            current_task: 当前任务描述
            k: 返回数量

        Returns:
            推荐Skill列表
        """
        return self._semantic_memory.get_skill_recommendations(
            current_op_type=current_op_type,
            current_task=current_task,
            k=k,
        )

    # ==================== Unified Retrieve ====================

    def retrieve(
        self,
        query: str,
        layers: Optional[list[str]] = None,
        k: int = 5,
    ) -> dict[str, list]:
        """统一检索接口

        自动路由到合适的记忆层。

        Args:
            query: 检索query
            layers: 要检索的层级列表，默认所有层
            k: 每层返回数量

        Returns:
            各层检索结果字典
        """
        if layers is None:
            layers = ["working", "episodic", "semantic"]

        results = {}

        if "working" in layers:
            # Working Memory: 直接返回相关记忆（简单关键词匹配）
            working_results = self._retrieve_from_working(query, k)
            results["working"] = working_results

        if "episodic" in layers:
            # Episodic Memory: 相似会话检索
            episodic_results = self._episodic_memory.search_similar_episodes(query, k)
            results["episodic"] = episodic_results

        if "semantic" in layers:
            # Semantic Memory: 混合检索
            semantic_results = self._semantic_memory.semantic_search(query, k)
            results["semantic"] = [
                {"name": s.name, "description": s.description, "content": s.content}
                for s in semantic_results
            ]

        return results

    def _retrieve_from_working(self, query: str, k: int) -> list[str]:
        """从WorkingMemory简单检索

        Args:
            query: 查询字符串
            k: 返回数量

        Returns:
            匹配的回忆列表
        """
        # 简单的关键词包含检查
        query_lower = query.lower()
        matches = []

        for pool in ["memory", "user"]:
            for item in self._working_memory.get(pool):
                if query_lower in item.lower():
                    matches.append(item)

        return matches[:k]

    # ==================== Session Management ====================

    def clear_working_memory(self, pool: Optional[str] = None) -> None:
        """清除工作记忆

        Args:
            pool: 记忆池名称，None表示清除所有
        """
        self._working_memory.clear(pool)

    def get_current_episode(self) -> Optional[Any]:
        """获取当前会话片段"""
        return self._episodic_memory.get_current_episode()
