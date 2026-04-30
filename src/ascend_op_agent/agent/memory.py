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
"""

from typing import Optional


class MemoryStore:
    """记忆存储

    支持:
    - 多pool分离 (memory: Agent记忆, user: 用户偏好)
    - 会话级快照冻结
    """

    def __init__(self):
        self._memory_pools: dict[str, list[str]] = {
            "memory": [],  # Agent记忆
            "user": [],    # 用户偏好
        }
        self._snapshot: Optional[dict[str, list[str]]] = None  # 会话级冻结快照

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

    def get(self, pool: str) -> list[str]:
        """获取记忆

        Args:
            pool: 记忆池名称

        Returns:
            记忆列表
        """
        return self._memory_pools.get(pool, [])

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
