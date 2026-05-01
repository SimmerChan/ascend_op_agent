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

"""FileSyncManager - 增强的文件同步管理

支持 hash 缓存、批量上传、增量同步。
基于 Hermes FileSyncManager 设计。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 默认同步间隔（秒）
_SYNC_INTERVAL_SECONDS = 5.0
_FORCE_SYNC_ENV = "ASCEND_FORCE_FILE_SYNC"

# 传输回调类型
UploadFn = Callable[[str, str], None]  # (host_path, remote_path) -> raises on failure
BulkUploadFn = Callable[[list[tuple[str, str]]], None]  # [(host_path, remote_path), ...] -> raises on failure
DeleteFn = Callable[[list[str]], None]  # (remote_paths) -> raises on failure
GetFilesFn = Callable[[], list[tuple[str, str]]]  # () -> [(host_path, remote_path), ...]


class SyncDirection(Enum):
    """同步方向"""
    PUSH = "push"  # 本地到远程
    PULL = "pull"  # 远程到本地


class SyncResult:
    """同步结果"""

    def __init__(
        self,
        success: bool,
        direction: SyncDirection,
        message: str = "",
        files_synced: int = 0,
        bytes_transferred: int = 0,
    ):
        self.success = success
        self.direction = direction
        self.message = message
        self.files_synced = files_synced
        self.bytes_transferred = bytes_transferred


def _sha256_file(path: str) -> str:
    """Return hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_mtime_key(path: str) -> Optional[tuple[float, int]]:
    """Get (mtime, size) for a file, or None if it doesn't exist."""
    try:
        st = os.stat(path)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def quoted_rm_command(remote_paths: list[str]) -> str:
    """Build a shell ``rm -f`` command for a batch of remote paths."""
    return "rm -f " + " ".join(shlex.quote(p) for p in remote_paths)


def quoted_mkdir_command(dirs: list[str]) -> str:
    """Build a shell ``mkdir -p`` command for a batch of directories."""
    return "mkdir -p " + " ".join(shlex.quote(d) for d in dirs)


def unique_parent_dirs(files: list[tuple[str, str]]) -> list[str]:
    """Extract sorted unique parent directories from (host, remote) pairs."""
    return sorted({str(Path(remote).parent) for _, remote in files})


class FileSyncManager:
    """增强的文件同步管理器

    特性：
    - mtime + size 变化检测
    - SHA-256 内容哈希缓存
    - 批量上传（tar-over-SSH）
    - 同步回拉（sync-back）
    - 事务性状态管理
    """

    def __init__(
        self,
        get_files_fn: GetFilesFn,
        upload_fn: Optional[UploadFn] = None,
        delete_fn: Optional[DeleteFn] = None,
        sync_interval: float = _SYNC_INTERVAL_SECONDS,
        bulk_upload_fn: Optional[BulkUploadFn] = None,
    ):
        """
        Args:
            get_files_fn: 获取待同步文件列表的函数
            upload_fn: 单文件上传函数 (host_path, remote_path)
            delete_fn: 批量删除函数 (remote_paths)
            sync_interval: 同步间隔（秒）
            bulk_upload_fn: 批量上传函数 (files)
        """
        self._get_files_fn = get_files_fn
        self._upload_fn = upload_fn
        self._delete_fn = delete_fn
        self._bulk_upload_fn = bulk_upload_fn
        self._synced_files: dict[str, tuple[float, int]] = {}  # remote_path -> (mtime, size)
        self._pushed_hashes: dict[str, str] = {}  # remote_path -> sha256 hex digest
        self._last_sync_time: float = 0.0  # monotonic; 0 ensures first sync runs
        self._sync_interval = sync_interval

    def sync(self, *, force: bool = False) -> None:
        """执行同步周期：上传 changed 文件，删除 removed 文件

        Rate-limited to once per ``sync_interval`` unless *force* is True
        or ``ASCEND_FORCE_FILE_SYNC=1`` is set.

        事务性：状态只在所有操作成功时才提交。
        失败时回滚状态，以便下次周期重试。
        """
        if not force and not os.environ.get(_FORCE_SYNC_ENV):
            now = time.monotonic()
            if now - self._last_sync_time < self._sync_interval:
                return

        current_files = self._get_files_fn()
        current_remote_paths = {remote for _, remote in current_files}

        # --- Uploads: new or changed files ---
        to_upload: list[tuple[str, str]] = []
        new_files = dict(self._synced_files)
        for host_path, remote_path in current_files:
            file_key = _file_mtime_key(host_path)
            if file_key is None:
                continue
            if self._synced_files.get(remote_path) == file_key:
                continue
            to_upload.append((host_path, remote_path))
            new_files[remote_path] = file_key

        # --- Deletes: synced paths no longer in current set ---
        to_delete = [p for p in self._synced_files if p not in current_remote_paths]

        if not to_upload and not to_delete:
            self._last_sync_time = time.monotonic()
            return

        # Snapshot for rollback (only when there's work to do)
        prev_files = dict(self._synced_files)
        prev_hashes = dict(self._pushed_hashes)

        if to_upload:
            logger.debug("file_sync: uploading %d file(s)", len(to_upload))
        if to_delete:
            logger.debug("file_sync: deleting %d stale remote file(s)", len(to_delete))

        try:
            if to_upload and self._bulk_upload_fn is not None:
                self._bulk_upload_fn(to_upload)
                logger.debug("file_sync: bulk-uploaded %d file(s)", len(to_upload))
            else:
                for host_path, remote_path in to_upload:
                    if self._upload_fn:
                        self._upload_fn(host_path, remote_path)
                    logger.debug("file_sync: uploaded %s -> %s", host_path, remote_path)

            if to_delete:
                if self._delete_fn:
                    self._delete_fn(to_delete)
                logger.debug("file_sync: deleted %s", to_delete)

            # --- Commit (all succeeded) ---
            for host_path, remote_path in to_upload:
                self._pushed_hashes[remote_path] = _sha256_file(host_path)

            for p in to_delete:
                new_files.pop(p, None)
                self._pushed_hashes.pop(p, None)

            self._synced_files = new_files
            self._last_sync_time = time.monotonic()

        except Exception as exc:
            self._synced_files = prev_files
            self._pushed_hashes = prev_hashes
            self._last_sync_time = time.monotonic()
            logger.warning("file_sync: sync failed, rolled back state: %s", exc)

    @property
    def pushed_hashes(self) -> dict[str, str]:
        """返回已推送文件的哈希缓存（用于 sync-back）"""
        return self._pushed_hashes


# =============================================================================
# 兼容旧的 FileSync API
# =============================================================================


class FileSync:
    """文件同步器（兼容旧 API）

    使用 rsync 进行增量同步，支持 checksum 验证。
    """

    def __init__(
        self,
        ssh_manager,  # SSHManager/SSHEnvironment instance
        default_remote_path: str = "/tmp",
    ):
        """
        Args:
            ssh_manager: SSHManager 或 SSHEnvironment 实例
            default_remote_path: 默认远程路径
        """
        self.ssh_manager = ssh_manager
        self.default_remote_path = default_remote_path

    def sync(
        self,
        local_path: str,
        remote_path: Optional[str] = None,
        direction: SyncDirection = SyncDirection.PUSH,
        exclude_patterns: Optional[list[str]] = None,
    ) -> SyncResult:
        """同步文件或目录

        Args:
            local_path: 本地路径
            remote_path: 远程路径（默认为 default_remote_path）
            direction: 同步方向
            exclude_patterns: 排除模式列表

        Returns:
            同步结果
        """
        remote_path = remote_path or self.default_remote_path

        # 确保SSH连接
        if hasattr(self.ssh_manager, "ensure_connected"):
            self.ssh_manager.ensure_connected()

        try:
            if direction == SyncDirection.PUSH:
                return self._push(local_path, remote_path, exclude_patterns)
            else:
                return self._pull(local_path, remote_path, exclude_patterns)
        except Exception as e:
            return SyncResult(
                success=False,
                direction=direction,
                message=f"同步失败: {e}",
            )

    def _push(
        self,
        local_path: str,
        remote_path: str,
        exclude_patterns: Optional[list[str]],
    ) -> SyncResult:
        """推送到远程"""
        local_path = os.path.abspath(local_path)

        if not os.path.exists(local_path):
            return SyncResult(
                success=False,
                direction=SyncDirection.PUSH,
                message=f"本地路径不存在: {local_path}",
            )

        # 构建rsync命令
        rsync_cmd = ["rsync", "-avz", "--progress"]

        # 添加checksum验证
        rsync_cmd.append("--checksum")

        # 排除模式
        if exclude_patterns:
            for pattern in exclude_patterns:
                rsync_cmd.extend(["--exclude", pattern])

        # 默认排除项
        rsync_cmd.extend([
            "--exclude", ".git",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", ".pytest_cache",
        ])

        # SSH隧道
        ssh_opts = f"-p {self.ssh_manager.port}"
        if hasattr(self.ssh_manager, "key_path") and self.ssh_manager.key_path:
            ssh_opts += f" -i {self.ssh_manager.key_path}"
        rsync_cmd.extend(["-e", f"ssh {ssh_opts}"])

        # 源和目标
        rsync_cmd.append(f"{local_path}/")
        rsync_cmd.append(f"{self.ssh_manager.user}@{self.ssh_manager.host}:{remote_path}/")

        # 执行rsync
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
        )

        # 解析输出
        files_synced = self._parse_rsync_output(result.stdout)

        return SyncResult(
            success=result.returncode == 0,
            direction=SyncDirection.PUSH,
            message=result.stdout if result.returncode == 0 else result.stderr,
            files_synced=files_synced,
        )

    def _pull(
        self,
        local_path: str,
        remote_path: str,
        exclude_patterns: Optional[list[str]],
    ) -> SyncResult:
        """从远程拉取"""
        local_path = os.path.abspath(local_path)

        # 确保本地目录存在
        os.makedirs(local_path, exist_ok=True)

        # 构建rsync命令
        rsync_cmd = ["rsync", "-avz", "--progress"]

        # 添加checksum验证
        rsync_cmd.append("--checksum")

        # 排除模式
        if exclude_patterns:
            for pattern in exclude_patterns:
                rsync_cmd.extend(["--exclude", pattern])

        # 默认排除项
        rsync_cmd.extend([
            "--exclude", ".git",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
        ])

        # SSH隧道
        ssh_opts = f"-p {self.ssh_manager.port}"
        if hasattr(self.ssh_manager, "key_path") and self.ssh_manager.key_path:
            ssh_opts += f" -i {self.ssh_manager.key_path}"
        rsync_cmd.extend(["-e", f"ssh {ssh_opts}"])

        # 源和目标 (反向)
        rsync_cmd.append(f"{self.ssh_manager.user}@{self.ssh_manager.host}:{remote_path}/")
        rsync_cmd.append(f"{local_path}/")

        # 执行rsync
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
        )

        # 解析输出
        files_synced = self._parse_rsync_output(result.stdout)

        return SyncResult(
            success=result.returncode == 0,
            direction=SyncDirection.PULL,
            message=result.stdout if result.returncode == 0 else result.stderr,
            files_synced=files_synced,
        )

    def _parse_rsync_output(self, output: str) -> int:
        """解析rsync输出，统计同步文件数"""
        import re

        match = re.search(r"(\d+)\s+files? to consider", output)
        if match:
            return int(match.group(1))

        match = re.search(r"sent\s+(\d+)\s+bytes", output)
        if match:
            return int(match.group(1))

        return 0

    @staticmethod
    def compute_file_hash(filepath: str) -> str:
        """计算文件MD5哈希"""
        hash_md5 = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()