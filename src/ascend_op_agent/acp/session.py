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

"""ACP 会话管理模块

管理编辑器会话状态，包括会话生命周期、超时处理和自动清理。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ACPSession:
    """ACP 会话对象"""

    session_id: str
    editor_info: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def update_activity(self) -> None:
        """更新最后活动时间"""
        self.last_activity = time.time()

    def is_expired(self, timeout_seconds: float) -> bool:
        """检查会话是否超时

        Args:
            timeout_seconds: 超时时间（秒）

        Returns:
            是否超时
        """
        return (time.time() - self.last_activity) > timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "editor_info": self.editor_info,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "context": self.context,
            "capabilities": self.capabilities,
        }


class SessionManager:
    """ACP 会话管理器

    管理编辑器会话状态，支持超时自动清理。
    """

    DEFAULT_TIMEOUT = 30 * 60  # 默认 30 分钟

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT):
        """初始化会话管理器

        Args:
            timeout_seconds: 会话超时时间，默认 30 分钟
        """
        self._sessions: dict[str, ACPSession] = {}
        self._timeout = timeout_seconds
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    def create_session(self, session_id: str, editor_info: dict[str, Any]) -> ACPSession:
        """创建新会话

        Args:
            session_id: 会话 ID
            editor_info: 编辑器信息

        Returns:
            创建的会话对象
        """
        session = ACPSession(
            session_id=session_id,
            editor_info=editor_info,
        )
        self._sessions[session_id] = session
        logger.info(f"Session created: {session_id}, editor: {editor_info.get('name', 'unknown')}")
        return session

    def get_session(self, session_id: str) -> Optional[ACPSession]:
        """获取会话

        Args:
            session_id: 会话 ID

        Returns:
            会话对象，如果不存在返回 None
        """
        return self._sessions.get(session_id)

    def update_session(self, session_id: str) -> bool:
        """更新会话活动时间

        Args:
            session_id: 会话 ID

        Returns:
            是否成功
        """
        session = self._sessions.get(session_id)
        if session:
            session.update_activity()
            return True
        return False

    def remove_session(self, session_id: str) -> bool:
        """删除会话

        Args:
            session_id: 会话 ID

        Returns:
            是否成功删除
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Session removed: {session_id}")
            return True
        return False

    def reset_session(self, session_id: str) -> bool:
        """重置会话上下文

        Args:
            session_id: 会话 ID

        Returns:
            是否成功
        """
        session = self._sessions.get(session_id)
        if session:
            session.context.clear()
            session.update_activity()
            logger.info(f"Session reset: {session_id}")
            return True
        return False

    def get_all_sessions(self) -> list[ACPSession]:
        """获取所有会话"""
        return list(self._sessions.values())

    def cleanup_expired(self) -> int:
        """清理超时会话

        Returns:
            清理的会话数量
        """
        expired_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if session.is_expired(self._timeout)
        ]
        for session_id in expired_ids:
            self.remove_session(session_id)
        if expired_ids:
            logger.info(f"Cleaned up {len(expired_ids)} expired sessions")
        return len(expired_ids)

    async def start_cleanup_task(self, interval_seconds: float = 60) -> None:
        """启动定期清理任务

        Args:
            interval_seconds: 清理间隔，默认 60 秒
        """
        self._running = True
        while self._running:
            await asyncio.sleep(interval_seconds)
            cleaned = self.cleanup_expired()
            if cleaned > 0:
                logger.debug(f"Periodic cleanup: {cleaned} sessions removed")

    def stop_cleanup_task(self) -> None:
        """停止定期清理任务"""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None

    @property
    def session_count(self) -> int:
        """当前会话数量"""
        return len(self._sessions)

    @property
    def timeout_seconds(self) -> float:
        """获取超时时间"""
        return self._timeout

    def set_timeout(self, timeout_seconds: float) -> None:
        """设置超时时间

        Args:
            timeout_seconds: 超时时间（秒）
        """
        self._timeout = timeout_seconds
