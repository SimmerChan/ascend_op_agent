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

"""ACP 会话管理器单元测试"""

import pytest
import time
from ascend_op_agent.acp.session import ACPSession, SessionManager


class TestACPSession:
    """ACP 会话测试"""

    def test_session_creation(self):
        """测试会话创建"""
        session = ACPSession(
            session_id="test-123",
            editor_info={"name": "VS Code"},
        )

        assert session.session_id == "test-123"
        assert session.editor_info["name"] == "VS Code"
        assert session.created_at > 0
        assert session.last_activity > 0

    def test_update_activity(self):
        """测试更新活动时间"""
        session = ACPSession(
            session_id="test-123",
            editor_info={},
        )
        original_activity = session.last_activity

        time.sleep(0.01)  # 短暂等待
        session.update_activity()

        assert session.last_activity >= original_activity

    def test_is_expired_not_expired(self):
        """测试会话未超时"""
        session = ACPSession(
            session_id="test-123",
            editor_info={},
        )

        # 1 秒超时，当前会话刚更新，不应超时
        assert session.is_expired(1) is False

    def test_is_expired_when_old(self):
        """测试会话超时"""
        session = ACPSession(
            session_id="test-123",
            editor_info={},
        )
        # 手动设置很旧的 last_activity
        session.last_activity = time.time() - 10

        # 1 秒超时，会话已超时
        assert session.is_expired(1) is True

    def test_to_dict(self):
        """测试会话转字典"""
        session = ACPSession(
            session_id="test-123",
            editor_info={"name": "VS Code"},
        )
        session.context["key"] = "value"

        data = session.to_dict()

        assert data["session_id"] == "test-123"
        assert data["editor_info"]["name"] == "VS Code"
        assert data["context"]["key"] == "value"


class TestSessionManager:
    """会话管理器测试"""

    def test_create_session(self):
        """测试创建会话"""
        manager = SessionManager(timeout_seconds=60)
        session = manager.create_session(
            session_id="test-123",
            editor_info={"name": "VS Code"},
        )

        assert session.session_id == "test-123"
        assert manager.session_count == 1

    def test_get_session(self):
        """测试获取会话"""
        manager = SessionManager()
        manager.create_session("test-123", {"name": "VS Code"})

        session = manager.get_session("test-123")
        assert session is not None
        assert session.session_id == "test-123"

        # 获取不存在的会话
        missing = manager.get_session("nonexistent")
        assert missing is None

    def test_remove_session(self):
        """测试删除会话"""
        manager = SessionManager()
        manager.create_session("test-123", {})

        result = manager.remove_session("test-123")
        assert result is True
        assert manager.session_count == 0

        # 删除不存在的会话
        result = manager.remove_session("nonexistent")
        assert result is False

    def test_reset_session(self):
        """测试重置会话"""
        manager = SessionManager()
        session = manager.create_session("test-123", {})
        session.context["key"] = "value"

        result = manager.reset_session("test-123")
        assert result is True
        assert session.context == {}  # 上下文已清空

        # 重置不存在的会话
        result = manager.reset_session("nonexistent")
        assert result is False

    def test_update_session(self):
        """测试更新会话"""
        manager = SessionManager()
        session = manager.create_session("test-123", {})

        result = manager.update_session("test-123")
        assert result is True

        # 更新不存在的会话
        result = manager.update_session("nonexistent")
        assert result is False

    def test_cleanup_expired(self):
        """测试清理超时会话"""
        manager = SessionManager(timeout_seconds=1)  # 1 秒超时

        # 创建一个会话
        session = manager.create_session("test-123", {})
        session.last_activity = time.time() - 10  # 设为已超时

        # 清理
        cleaned = manager.cleanup_expired()
        assert cleaned == 1
        assert manager.session_count == 0

    def test_cleanup_not_expired(self):
        """测试不清理未超时会话"""
        manager = SessionManager(timeout_seconds=60)

        session = manager.create_session("test-123", {})
        # last_activity 保持默认（当前时间）

        cleaned = manager.cleanup_expired()
        assert cleaned == 0
        assert manager.session_count == 1

    def test_get_all_sessions(self):
        """测试获取所有会话"""
        manager = SessionManager()
        manager.create_session("test-1", {"name": "VS Code"})
        manager.create_session("test-2", {"name": "Zed"})

        sessions = manager.get_all_sessions()
        assert len(sessions) == 2

    def test_timeout_configuration(self):
        """测试超时配置"""
        manager = SessionManager(timeout_seconds=120)

        assert manager.timeout_seconds == 120
        manager.set_timeout(300)
        assert manager.timeout_seconds == 300
