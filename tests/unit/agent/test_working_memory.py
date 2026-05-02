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

"""WorkingMemory单元测试"""

import pytest

from ascend_op_agent.agent.memory import MemoryStore


class TestWorkingMemory:
    """WorkingMemory增强功能测试"""

    def test_context_compression_threshold(self):
        """测试上下文压缩阈值"""
        # 使用小阈值以便测试
        store = MemoryStore(context_threshold=100, compression_keep=3)

        # 添加足够多的记忆使总大小超过阈值
        for i in range(10):
            store.add("memory", f"记忆{i}这是一段比较长的记忆内容")

        # 应该有压缩发生
        items = store.get("memory")
        assert len(items) < 10
        # 应该有压缩标记
        assert any("已压缩" in item for item in items)

    def test_compression_keeps_recent(self):
        """测试压缩保留最近的消息"""
        store = MemoryStore(context_threshold=50, compression_keep=2)

        # 添加多个记忆
        store.add("memory", "第一个记忆")
        store.add("memory", "第二个记忆")
        store.add("memory", "第三个记忆")
        store.add("memory", "第四个记忆")

        items = store.get("memory")

        # 最近的消息应该保留
        assert "第四个记忆" in items
        assert "第三个记忆" in items
        assert "第二个记忆" in items

    def test_get_total_size(self):
        """测试获取记忆总大小"""
        store = MemoryStore(context_threshold=1000)
        store.add("memory", "短")
        store.add("memory", "中等长度记忆")

        assert store.get_total_size("memory") == len("短") + len("中等长度记忆")

    def test_get_full_context(self):
        """测试获取完整上下文"""
        store = MemoryStore()
        store.add("memory", "记忆1")
        store.add("memory", "记忆2")

        context = store.get_full_context("memory")

        assert "记忆1" in context
        assert "记忆2" in context

    def test_compression_preserves_user_pool(self):
        """测试压缩只影响memory池不影响user池"""
        store = MemoryStore(context_threshold=50, compression_keep=1)

        # 添加很多memory记忆
        for i in range(5):
            store.add("memory", f"memory记忆{i}这是一段比较长的内容")

        # 添加一些user记忆
        for i in range(5):
            store.add("user", f"user记忆{i}")

        # user池不应该被压缩
        user_items = store.get("user")
        assert len(user_items) == 5

    def test_snapshot_with_compression(self):
        """测试快照与压缩的交互"""
        store = MemoryStore(context_threshold=50, compression_keep=2)

        store.add("memory", "原始记忆1")
        store.add("memory", "原始记忆2")

        # 冻结前添加更多记忆（触发压缩）
        store.add("memory", "新记忆A这是一段比较长的内容")
        store.add("memory", "新记忆B这是一段比较长的内容")

        # 冻结
        store.freeze_snapshot()

        # 恢复
        store.restore_snapshot()

        # 新记忆应该被移除
        assert "新记忆A" not in store.get("memory")
        assert "新记忆B" not in store.get("memory")
