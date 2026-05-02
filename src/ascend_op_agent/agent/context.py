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

"""ContextEngine - 上下文压缩和检索

参考Hermes Agent的ContextEngine实现:
- 优先级互斥加载模式
- 安全扫描防止注入
- 混合检索（基于SemanticMemory）
"""

import logging
import os
import re
from typing import Optional

from ascend_op_agent.memory.semantic_memory import SemanticMemory

logger = logging.getLogger(__name__)


class ContextEngine:
    """上下文引擎

    负责:
    - 上下文文件加载（优先级互斥模式）
    - 上下文压缩
    - 安全扫描
    - 基于SemanticMemory的上下文检索
    """

    # 优先级文件列表（按优先级从高到低）
    PRIORITY_FILES = ['.hermes.md', 'AGENTS.md', 'CLAUDE.md', '.cursorrules']

    def __init__(
        self,
        max_tokens: int = 128000,
        semantic_memory: Optional[SemanticMemory] = None,
    ):
        """
        Args:
            max_tokens: 最大token数（用于上下文压缩）
            semantic_memory: SemanticMemory实例（用于检索）
        """
        self.max_tokens = max_tokens
        self._context_cache: dict[str, str] = {}
        self._semantic_memory = semantic_memory or SemanticMemory()

    def build_context_prompt(self, workspace_path: str) -> str:
        """构建上下文Prompt

        采用优先级互斥模式：只加载一个最高优先级的文件

        Args:
            workspace_path: 工作区路径

        Returns:
            上下文Prompt
        """
        for filename in self.PRIORITY_FILES:
            filepath = os.path.join(workspace_path, filename)
            if os.path.exists(filepath):
                return self._load_and_scan(filepath)
        return ""

    def _load_and_scan(self, filepath: str) -> str:
        """加载文件并做安全扫描

        Args:
            filepath: 文件路径

        Returns:
            处理后的文件内容
        """
        if filepath in self._context_cache:
            return self._context_cache[filepath]

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # 安全扫描
        content = self._sanitize(content)

        # 缓存
        self._context_cache[filepath] = content
        return content

    def _sanitize(self, content: str) -> str:
        """安全扫描：防止提示词注入

        Args:
            content: 原始内容

        Returns:
            扫描后的安全内容
        """
        # 不可见字符
        invisible_patterns = [
            r'\x00',  # null byte
            r'\u200b',  # zero-width space
            r'\u202b',  # right-to-left embedding
            r'\ufeff',  # byte order mark
        ]
        for pattern in invisible_patterns:
            content = re.sub(pattern, '', content)

        # 中文威胁词（示例）
        threat_patterns = [
            r'[\梯队]',
            r'软体',
        ]
        for pattern in threat_patterns:
            content = re.sub(pattern, '', content)

        return content

    def retrieve(
        self,
        query: str,
        k: int = 5,
        op_type: Optional[str] = None,
        use_hybrid: bool = True,
    ) -> list[str]:
        """检索相关上下文

        基于SemanticMemory的混合检索获取相关上下文。

        Args:
            query: 查询字符串
            k: 返回结果数量
            op_type: 可选的算子类型过滤
            use_hybrid: 是否使用混合检索

        Returns:
            相关上下文片段列表
        """
        try:
            results = self._semantic_memory.semantic_search(
                query=query,
                k=k,
                op_type=op_type,
                use_hybrid=use_hybrid,
            )

            # 提取content作为返回结果
            contexts = []
            for skill in results:
                contexts.append(skill.content)

            return contexts

        except Exception as e:
            logger.warning(f"Failed to retrieve contexts: {e}")
            return []

    def retrieve_as_dict(
        self,
        query: str,
        k: int = 5,
        op_type: Optional[str] = None,
    ) -> list[dict]:
        """检索相关上下文（返回完整信息）

        Args:
            query: 查询字符串
            k: 返回结果数量
            op_type: 可选的算子类型过滤

        Returns:
            相关上下文字典列表，包含name、description、content、tags
        """
        try:
            results = self._semantic_memory.semantic_search(
                query=query,
                k=k,
                op_type=op_type,
                use_hybrid=True,
            )

            return [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "content": skill.content,
                    "tags": skill.tags,
                }
                for skill in results
            ]

        except Exception as e:
            logger.warning(f"Failed to retrieve contexts as dict: {e}")
            return []

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
            推荐Skill列表，包含相似度和推荐理由
        """
        try:
            return self._semantic_memory.get_skill_recommendations(
                current_op_type=current_op_type,
                current_task=current_task,
                k=k,
            )
        except Exception as e:
            logger.warning(f"Failed to get skill recommendations: {e}")
            return []

    def clear_cache(self) -> None:
        """清除上下文缓存"""
        self._context_cache.clear()
