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

"""FileSync - 文件同步

使用rsync进行增量文件同步。
"""

import hashlib
import os
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional


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


class FileSync:
    """文件同步器

    使用rsync进行增量同步，支持checksum验证。
    """

    def __init__(
        self,
        ssh_manager,  # SSHManager instance
        default_remote_path: str = "/tmp",
    ):
        """
        Args:
            ssh_manager: SSHManager实例
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
        if self.ssh_manager.key_path:
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
        if self.ssh_manager.key_path:
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
        # rsync -v 输出包含 "sent X bytes" 和 "received Y bytes"
        # 或者 "X files to consider"
        import re

        match = re.search(r'(\d+)\s+files? to consider', output)
        if match:
            return int(match.group(1))

        match = re.search(r'sent\s+(\d+)\s+bytes', output)
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
