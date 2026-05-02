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

"""EpisodicMemory单元测试"""

import tempfile

import pytest

from ascend_op_agent.memory.episodic_memory import (
    ConversationTurn,
    Episode,
    EpisodicMemory,
)


class TestEpisodicMemory:
    """EpisodicMemory测试"""

    def test_creation(self):
        """测试创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = EpisodicMemory()

            assert memory._current_episode is None
            assert memory._episode_counter == 0

    def test_start_episode(self):
        """测试开始会话片段"""
        memory = EpisodicMemory()

        episode_id = memory.start_episode()

        assert episode_id.startswith("episode_")
        assert memory._current_episode is not None
        assert memory._current_episode.episode_id == episode_id

    def test_start_episode_with_custom_id(self):
        """测试使用自定义ID开始会话片段"""
        memory = EpisodicMemory()

        episode_id = memory.start_episode("my-custom-episode")

        assert episode_id == "my-custom-episode"
        assert memory._current_episode.episode_id == "my-custom-episode"

    def test_add_turn(self):
        """测试添加对话轮次"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")

        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi there")

        assert memory._current_episode.message_count == 2
        assert memory._current_episode.turns[0].role == "user"
        assert memory._current_episode.turns[0].content == "Hello"

    def test_add_turn_auto_start(self):
        """测试自动开始会话片段"""
        memory = EpisodicMemory()

        # 不手动start，直接add
        memory.add_turn("user", "Hello")

        assert memory._current_episode is not None
        assert memory._current_episode.message_count == 1

    def test_end_episode(self):
        """测试结束会话片段"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")
        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi")

        episode = memory.end_episode()

        assert episode is not None
        assert episode.episode_id == "test-episode"
        assert episode.message_count == 2
        assert memory._current_episode is None

    def test_summarize_when_threshold_reached(self):
        """测试达到阈值时生成摘要"""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = EpisodicMemory(
                message_threshold=3,
                token_threshold=1000,
                summarize_episodes=True,
            )

            memory.start_episode("test-episode")
            memory.add_turn("user", "First message")
            memory.add_turn("user", "Second message")
            memory.add_turn("user", "Third message")

            # 应该已生成摘要
            assert memory._current_episode.summary is not None
            assert "会话摘要" in memory._current_episode.summary

    def test_no_summarize_when_disabled(self):
        """测试禁用摘要时不生成"""
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = EpisodicMemory(
                message_threshold=2,
                summarize_episodes=False,
            )

            memory.start_episode("test-episode")
            memory.add_turn("user", "First")
            memory.add_turn("user", "Second")
            memory.add_turn("user", "Third")

            # 不应该生成摘要
            assert memory._current_episode.summary is None

    def test_episode_content_conversion(self):
        """测试会话片段内容转换"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")
        memory.add_turn("user", "Hello")
        memory.add_turn("assistant", "Hi there")

        content = memory._episode_to_content(memory._current_episode)

        assert "test-episode" in content
        assert "[user]: Hello" in content
        assert "[assistant]: Hi there" in content

    def test_episode_message_count(self):
        """测试消息计数"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")

        for i in range(5):
            memory.add_turn("user", f"Message {i}")

        assert memory._current_episode.message_count == 5

    def test_episode_total_tokens(self):
        """测试token估算"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")

        memory.add_turn("user", "This is a test message with some content")

        # 每个字符约等于1/4 token
        expected_tokens = len("This is a test message with some content") // 4
        assert memory._current_episode.total_tokens >= expected_tokens - 1
        assert memory._current_episode.total_tokens <= expected_tokens + 1

    def test_get_current_episode(self):
        """测试获取当前会话片段"""
        memory = EpisodicMemory()
        memory.start_episode("test-episode")
        memory.add_turn("user", "Hello")

        current = memory.get_current_episode()

        assert current is not None
        assert current.episode_id == "test-episode"
        assert current.message_count == 1

    def test_get_current_episode_when_none(self):
        """测试没有当前会话片段时返回None"""
        memory = EpisodicMemory()

        assert memory.get_current_episode() is None


class TestConversationTurn:
    """ConversationTurn测试"""

    def test_creation(self):
        """测试创建"""
        turn = ConversationTurn(role="user", content="Hello")

        assert turn.role == "user"
        assert turn.content == "Hello"
        assert turn.timestamp > 0


class TestEpisode:
    """Episode测试"""

    def test_creation(self):
        """测试创建"""
        turns = [
            ConversationTurn(role="user", content="Hello"),
            ConversationTurn(role="assistant", content="Hi"),
        ]

        episode = Episode(
            episode_id="test-1",
            turns=turns,
            summary="Test summary",
        )

        assert episode.episode_id == "test-1"
        assert episode.message_count == 2
        assert episode.summary == "Test summary"

    def test_message_count(self):
        """测试消息计数"""
        episode = Episode(
            episode_id="test-1",
            turns=[
                ConversationTurn(role="user", content="1"),
                ConversationTurn(role="user", content="2"),
                ConversationTurn(role="user", content="3"),
            ],
        )

        assert episode.message_count == 3

    def test_total_tokens(self):
        """测试token估算"""
        episode = Episode(
            episode_id="test-1",
            turns=[
                ConversationTurn(role="user", content="Short"),
            ],
        )

        # 至少应该有一些token
        assert episode.total_tokens >= 0
