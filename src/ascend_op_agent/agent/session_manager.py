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

"""SessionRecordManager - 完整对话记录管理器

采用 JSONL Append-only 格式存储对话记录，使用 ThreadPoolExecutor + Queue 实现异步写入。
参考 Claude Code 的 sessionStorage.ts 设计。
"""

import atexit
import logging
import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
from typing import Optional

from ascend_op_agent.agent.session_record import Entry, chunk_content

logger = logging.getLogger(__name__)

# 刷新间隔（毫秒）
FLUSH_INTERVAL_MS = 100

# 最大单条记录字节数（100MB），超过则分块
MAX_CHUNK_BYTES = 100 * 1024 * 1024

# 默认会话存储目录
DEFAULT_SESSIONS_DIR = "~/.ascend_op_agent/sessions"


class SessionRecordManager:
    """完整对话记录管理器

    使用 JSONL Append-only 格式，通过线程池异步写入文件。
    """

    def __init__(
        self,
        persist_dir: str = DEFAULT_SESSIONS_DIR,
        flush_interval_ms: int = FLUSH_INTERVAL_MS,
        max_history: Optional[int] = None,
    ):
        """
        Args:
            persist_dir: 会话记录持久化目录
            flush_interval_ms: 刷新间隔（毫秒）
            max_history: 最大保存会话数（None 表示不限制）
        """
        self._persist_dir = Path(persist_dir).expanduser()
        self._flush_interval_ms = flush_interval_ms
        self._max_history = max_history

        # 确保目录存在
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        # 写入队列和线程池
        self._write_queue: Queue[str] = Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session_writer")
        self._write_future: Optional[Future] = None

        # 线程安全锁
        self._lock = Lock()

        # 当前打开的文件指针
        self._current_file: Optional[Path] = None
        self._current_file_handle: Optional[object] = None
        self._current_chunk_index = 0
        self._current_file_size = 0

        # 会话 ID 到文件路径的映射
        self._session_files: dict[str, Path] = {}

        # 注册清理函数
        atexit.register(self.shutdown)

    def _get_session_file(self, session_id: str) -> Path:
        """获取会话对应的 JSONL 文件路径

        Args:
            session_id: 会话 ID

        Returns:
            JSONL 文件路径
        """
        if session_id in self._session_files:
            return self._session_files[session_id]

        # 检查已存在的分块文件
        base_path = self._persist_dir / f"{session_id}.jsonl"
        if base_path.exists():
            self._session_files[session_id] = base_path
            return base_path

        # 查找是否有分块文件
        for i in range(1, 1000):
            chunk_path = self._persist_dir / f"{session_id}.jsonl.{i:03d}"
            if chunk_path.exists():
                # 使用最新的分块
                continue
            else:
                # 找到第一个不存在的分块号
                if i == 1:
                    self._session_files[session_id] = base_path
                else:
                    prev_chunk = self._persist_dir / f"{session_id}.jsonl.{i-1:03d}"
                    self._session_files[session_id] = prev_chunk
                return self._session_files[session_id]

        self._session_files[session_id] = base_path
        return base_path

    def _open_file(self, file_path: Path) -> object:
        """打开文件（支持追加写入）

        Args:
            file_path: 文件路径

        Returns:
            文件句柄
        """
        return open(file_path, "a", encoding="utf-8")

    def _close_current_file(self) -> None:
        """关闭当前打开的文件"""
        if self._current_file_handle is not None:
            try:
                self._current_file_handle.close()
            except Exception as e:
                logger.warning(f"Failed to close file: {e}")
            self._current_file_handle = None
            self._current_file = None
            self._current_file_size = 0

    def _rotate_file_if_needed(self, session_id: str) -> None:
        """检查是否需要轮转文件

        Args:
            session_id: 会话 ID
        """
        file_path = self._get_session_file(session_id)

        # 如果当前文件大小接近阈值，创建新分块
        if self._current_file_size >= MAX_CHUNK_BYTES:
            self._close_current_file()
            self._current_chunk_index += 1
            new_path = self._persist_dir / f"{session_id}.jsonl.{self._current_chunk_index:03d}"
            self._session_files[session_id] = new_path

    def append_entry(self, entry: Entry) -> None:
        """追加对话记录条目

        Args:
            entry: 对话记录条目
        """
        if entry.session_id:
            self._rotate_file_if_needed(entry.session_id)

        # 序列化为 JSON 行
        json_line = entry.to_json()

        # 检查单条记录大小
        line_size = len(json_line.encode("utf-8"))
        if line_size > MAX_CHUNK_BYTES:
            # 分块写入
            chunks = chunk_content(entry.content if hasattr(entry, "content") else "")
            for i, chunk in enumerate(chunks):
                # 创建一个新的 entry 用于每块
                chunk_entry = entry
                if hasattr(entry, "content"):
                    chunk_entry = entry.__class__(**{**entry.__dict__, "content": chunk})
                self._write_queue.put(chunk_entry.to_json())
        else:
            self._write_queue.put(json_line)

        # 确保写入线程在运行
        self._ensure_writer_running()

    def _ensure_writer_running(self) -> None:
        """确保写入线程在运行"""
        if self._write_future is None or self._write_future.done():
            self._write_future = self._executor.submit(self._write_loop)

    def _write_loop(self) -> None:
        """写入循环（在线程中运行）"""
        last_flush_time = time.time()
        pending_lines: list[str] = []

        while True:
            try:
                # 等待新条目或超时
                try:
                    line = self._write_queue.get(timeout=0.1)
                    pending_lines.append(line)
                except Empty:
                    pass

                # 检查是否应该刷新
                elapsed = (time.time() - last_flush_time) * 1000
                if elapsed >= self._flush_interval_ms and pending_lines:
                    self._drain_write_queue(pending_lines)
                    pending_lines = []
                    last_flush_time = time.time()

                # 检查是否应该退出（队列为空且收到停止信号）
                if self._write_queue.empty() and self._shutdown_requested:
                    # 最后一次刷新
                    if pending_lines:
                        self._drain_write_queue(pending_lines)
                    break

            except Exception as e:
                logger.error(f"Error in write loop: {e}")

    def _drain_write_queue(self, lines: list[str]) -> None:
        """批量写入行到文件

        Args:
            lines: 待写入的行列表
        """
        if not lines:
            return

        with self._lock:
            for line in lines:
                try:
                    # 解析 session_id
                    import json

                    data = json.loads(line)
                    session_id = data.get("session_id", "default")

                    file_path = self._get_session_file(session_id)

                    # 打开文件（如需要）
                    if self._current_file != file_path:
                        self._close_current_file()
                        self._current_file = file_path
                        self._current_file_handle = self._open_file(file_path)

                    # 写入行
                    self._current_file_handle.write(line + "\n")
                    self._current_file_size += len(line.encode("utf-8")) + 1

                    # 检查是否需要轮转
                    self._rotate_file_if_needed(session_id)

                except Exception as e:
                    logger.error(f"Failed to write line: {e}")

            # 刷新文件
            if self._current_file_handle:
                try:
                    self._current_file_handle.flush()
                except Exception as e:
                    logger.warning(f"Failed to flush file: {e}")

    _shutdown_requested = False

    def shutdown(self) -> None:
        """关闭管理器，确保所有缓冲写入完成"""
        self._shutdown_requested = True

        # 等待写入线程完成
        if self._write_future is not None:
            try:
                self._write_future.result(timeout=5.0)
            except Exception as e:
                logger.warning(f"Error waiting for write thread: {e}")

        # 关闭文件
        self._close_current_file()

        # 关闭线程池
        self._executor.shutdown(wait=False)

    def __del__(self) -> None:
        """析构时确保关闭"""
        try:
            self.shutdown()
        except Exception:
            pass


def read_session_history(
    session_id: str,
    persist_dir: str = DEFAULT_SESSIONS_DIR,
    limit: Optional[int] = None,
) -> list[Entry]:
    """读取会话历史

    Args:
        session_id: 会话 ID
        persist_dir: 会话记录持久化目录
        limit: 返回记录数限制（None 表示全部）

    Returns:
        Entry 列表
    """
    from ascend_op_agent.agent.session_record import entry_from_json

    persist_path = Path(persist_dir).expanduser()
    file_path = persist_path / f"{session_id}.jsonl"

    if not file_path.exists():
        return []

    entries = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(entry_from_json(line))
                except Exception as e:
                    logger.warning(f"Failed to parse line: {e}")

    if limit is not None:
        entries = entries[-limit:]

    return entries


def list_sessions(
    persist_dir: str = DEFAULT_SESSIONS_DIR,
    limit: int = 100,
) -> list[dict]:
    """列出所有会话

    Args:
        persist_dir: 会话记录持久化目录
        limit: 返回数量限制

    Returns:
        会话信息列表
    """
    persist_path = Path(persist_dir).expanduser()

    if not persist_path.exists():
        return []

    sessions = []
    for file_path in sorted(
        persist_path.glob("*.jsonl*"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        # 跳过目录
        if file_path.is_dir():
            continue

        name = file_path.name
        if name.endswith(".jsonl"):
            session_id = name[:-6]
        elif name.endswith(".jsonl.001"):
            session_id = name[:-12]
        elif name.endswith(".jsonl.002"):
            continue  # 跳过分块文件
        else:
            continue

        # 去重
        if any(s["session_id"] == session_id for s in sessions):
            continue

        sessions.append(
            {
                "session_id": session_id,
                "file_path": str(file_path),
                "modified_at": file_path.stat().st_mtime,
            }
        )

        if len(sessions) >= limit:
            break

    return sessions
