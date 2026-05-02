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

"""MemoryStore - 持久化记忆存储

参考Hermes Agent的MemoryStore设计:
- 多pool分离（memory/user）
- 快照冻结机制
- 上下文压缩
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 默认上下文压缩阈值（字符数）
DEFAULT_CONTEXT_THRESHOLD = 4000

# 压缩后保留的消息数
DEFAULT_COMPRESSION_KEEP = 10


class MemoryStore:
    """记忆存储

    支持:
    - 多pool分离 (memory: Agent记忆, user: 用户偏好)
    - 会话级快照冻结
    - 上下文压缩（当记忆超长时）
    """

    def __init__(
        self,
        context_threshold: int = DEFAULT_CONTEXT_THRESHOLD,
        compression_keep: int = DEFAULT_COMPRESSION_KEEP,
    ):
        """
        Args:
            context_threshold: 上下文压缩阈值（字符数）
            compression_keep: 压缩时保留的消息数
        """
        self._memory_pools: dict[str, list[str]] = {
            "memory": [],  # Agent记忆
            "user": [],    # 用户偏好
        }
        self._snapshot: Optional[dict[str, list[str]]] = None  # 会话级冻结快照
        self._context_threshold = context_threshold
        self._compression_keep = compression_keep

    def add(self, pool: str, content: str) -> None:
        """添加记忆

        Args:
            pool: 记忆池名称 (memory 或 user)
            content: 记忆内容
        """
        if pool not in self._memory_pools:
            self._memory_pools[pool] = []
        self._memory_pools[pool].append(content)
        # 注意：不自动失效快照，由用户决定何时失效

        # 自动检查是否需要压缩
        self._maybe_compress(pool)

    def _maybe_compress(self, pool: str) -> None:
        """检查是否需要压缩上下文

        Args:
            pool: 记忆池名称
        """
        items = self._memory_pools.get(pool, [])
        if not items:
            return

        # 计算总大小
        total_size = sum(len(item) for item in items)

        if total_size > self._context_threshold:
            self._compress_context(pool)

    def _compress_context(self, pool: str) -> None:
        """压缩上下文

        保留最近的消息，压缩旧消息为摘要

        Args:
            pool: 记忆池名称
        """
        items = self._memory_pools.get(pool, [])
        if len(items) <= self._compression_keep:
            return

        # 保留最近的消息
        kept_items = items[-self._compression_keep:]
        compressed_count = len(items) - self._compression_keep

        # 生成压缩摘要
        summary = f"[{compressed_count}条旧记忆已压缩]"

        # 更新记忆池
        self._memory_pools[pool] = [summary] + kept_items

        logger.info(f"Compressed {compressed_count} old memories in pool '{pool}'")

    def get(self, pool: str) -> list[str]:
        """获取记忆

        Args:
            pool: 记忆池名称

        Returns:
            记忆列表
        """
        return self._memory_pools.get(pool, [])

    def get_full_context(self, pool: str) -> str:
        """获取完整上下文（用于系统Prompt）

        Args:
            pool: 记忆池名称

        Returns:
            格式化后的记忆内容
        """
        items = self._memory_pools.get(pool, [])
        if not items:
            return ""
        return "\n".join(items)

    def format_for_system_prompt(self, pool: str) -> str:
        """格式化记忆用于系统Prompt

        Args:
            pool: 记忆池名称

        Returns:
            格式化后的记忆内容
        """
        items = self._memory_pools.get(pool, [])
        if not items:
            return ""
        return f"[{pool.upper()}]:\n" + "\n".join(items)

    def get_total_size(self, pool: str) -> int:
        """获取记忆总大小（字符数）

        Args:
            pool: 记忆池名称

        Returns:
            记忆总字符数
        """
        items = self._memory_pools.get(pool, [])
        return sum(len(item) for item in items)

    def freeze_snapshot(self) -> None:
        """冻结快照

        缓存一致性：会话级冻结，避免写入破坏缓存
        """
        self._snapshot = {k: list(v) for k, v in self._memory_pools.items()}

    def restore_snapshot(self) -> None:
        """恢复快照"""
        if self._snapshot:
            self._memory_pools = {k: list(v) for k, v in self._snapshot.items()}

    def _invalidate_snapshot(self) -> None:
        """使快照失效"""
        self._snapshot = None

    def clear(self, pool: Optional[str] = None) -> None:
        """清除记忆

        Args:
            pool: 记忆池名称，None表示清除所有
        """
        if pool is None:
            self._memory_pools = {"memory": [], "user": []}
        elif pool in self._memory_pools:
            self._memory_pools[pool] = []
        self._invalidate_snapshot()

    def size(self, pool: str) -> int:
        """获取记忆数量

        Args:
            pool: 记忆池名称

        Returns:
            记忆数量
        """
        return len(self._memory_pools.get(pool, []))
