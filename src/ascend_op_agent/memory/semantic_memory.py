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

"""SemanticMemory - 技能知识语义检索

基于 SkillIndex 构建语义层，支持:
- Skill 语义索引构建
- 场景化检索 (如"寻找 MatMul 算子经验")
- 多维度筛选 (类型、标签、场景)
"""

import logging
from typing import Any, Optional

from ascend_op_agent.skills import SkillIndex
from ascend_op_agent.skills.models import Skill

logger = logging.getLogger(__name__)


class SemanticMemory:
    """技能知识语义检索

    基于 SkillIndex 的混合检索能力，构建语义搜索层。
    """

    def __init__(
        self,
        skill_index: Optional[SkillIndex] = None,
    ):
        """
        Args:
            skill_index: SkillIndex实例
        """
        self._skill_index = skill_index or SkillIndex()

    def semantic_search(
        self,
        query: str,
        k: int = 5,
        op_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
        use_hybrid: bool = True,
    ) -> list[Skill]:
        """语义搜索Skill

        Args:
            query: 搜索query
            k: 返回数量
            op_type: 算子类型过滤
            tags: 标签过滤
            use_hybrid: 是否使用混合检索（FTS5 + 向量）

        Returns:
            匹配的Skill列表
        """
        if use_hybrid and hasattr(self._skill_index, "hybrid_search"):
            results = self._skill_index.hybrid_search(query, k=k * 2)
        else:
            results = self._skill_index.search(query, k=k * 2)

        # 应用过滤
        if op_type or tags:
            results = self._filter_skills(results, op_type, tags)

        return results[:k]

    def _filter_skills(
        self,
        skills: list[Skill],
        op_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[Skill]:
        """过滤Skill列表

        Args:
            skills: Skill列表
            op_type: 算子类型过滤
            tags: 标签过滤

        Returns:
            过滤后的Skill列表
        """
        filtered = []

        for skill in skills:
            metadata = skill.metadata or {}

            # 检查op_type
            if op_type:
                skill_op_type = metadata.get("op_type", "")
                if op_type not in skill_op_type:
                    continue

            # 检查tags
            if tags:
                skill_tags = skill.tags or []
                if not any(tag in skill_tags for tag in tags):
                    continue

            filtered.append(skill)

        return filtered

    def search_by_scenario(
        self,
        scenario: str,
        k: int = 5,
    ) -> list[Skill]:
        """按场景检索Skill

        Args:
            scenario: 场景描述，如 "MatMul算子开发"、"性能优化"
            k: 返回数量

        Returns:
            匹配的Skill列表
        """
        return self.semantic_search(scenario, k=k)

    def search_by_op_type(
        self,
        op_type: str,
        k: int = 5,
        scenario: Optional[str] = None,
    ) -> list[Skill]:
        """按算子类型检索Skill

        Args:
            op_type: 算子类型 (如 matmul, conv2d, relu)
            k: 返回数量
            scenario: 可选的场景描述

        Returns:
            匹配的Skill列表
        """
        query = scenario or op_type
        return self.semantic_search(query, k=k, op_type=op_type)

    def search_by_tags(
        self,
        tags: list[str],
        k: int = 5,
        scenario: Optional[str] = None,
    ) -> list[Skill]:
        """按标签检索Skill

        Args:
            tags: 标签列表
            k: 返回数量
            scenario: 可选的场景描述

        Returns:
            匹配的Skill列表
        """
        query = scenario or " ".join(tags)
        return self.semantic_search(query, k=k, tags=tags)

    def get_skill_recommendations(
        self,
        current_op_type: str,
        current_task: str,
        k: int = 3,
    ) -> list[dict[str, Any]]:
        """获取Skill推荐

        基于当前算子类型和任务，推荐相关的Skill。

        Args:
            current_op_type: 当前算子类型
            current_task: 当前任务描述
            k: 返回数量

        Returns:
            推荐Skill列表，包含相似度和推荐理由
        """
        # 搜索相关Skill
        results = self.semantic_search(
            f"{current_op_type} {current_task}",
            k=k * 2,
            op_type=current_op_type,
        )

        # 构建推荐结果
        recommendations = []
        for i, skill in enumerate(results[:k]):
            recommendations.append({
                "skill": skill,
                "similarity_score": 1.0 / (i + 1),  # 简化的相似度分数
                "reason": self._generate_recommendation_reason(skill, current_op_type),
            })

        return recommendations

    def _generate_recommendation_reason(
        self,
        skill: Skill,
        current_op_type: str,
    ) -> str:
        """生成推荐理由

        Args:
            skill: Skill对象
            current_op_type: 当前算子类型

        Returns:
            推荐理由
        """
        metadata = skill.metadata or {}

        if metadata.get("op_type") == current_op_type:
            return f"与当前{current_op_type}算子直接相关"

        if "template" in skill.tags:
            return "提供基础模板"

        if "bugfix" in skill.tags:
            return "包含Bug修复经验"

        if "performance" in skill.tags:
            return "包含性能优化经验"

        return "提供相关经验参考"

    def build_semantic_index(self, repository: Any) -> int:
        """构建语义索引

        从SkillRepository重建语义索引。

        Args:
            repository: SkillRepository实例

        Returns:
            索引的Skill数量
        """
        if hasattr(self._skill_index, "rebuild_index"):
            self._skill_index.rebuild_index(repository)
            logger.info("Semantic index rebuilt successfully")
            return len(repository.list_local_skills()) if hasattr(repository, "list_local_skills") else 0

        return 0
