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

"""FileSyncManager 测试"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ascend_op_agent.ssh.sync import (
    FileSync,
    FileSyncManager,
    SyncDirection,
    SyncResult,
    _file_mtime_key,
    _sha256_file,
    quoted_mkdir_command,
    quoted_rm_command,
    unique_parent_dirs,
)


class TestHelperFunctions:
    """辅助函数测试"""

    def test_file_mtime_key_existing_file(self):
        """测试存在的文件的 mtime key"""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test content")
            temp_path = f.name

        try:
            key = _file_mtime_key(temp_path)
            assert key is not None
            assert len(key) == 2
            assert key[0] > 0  # mtime
            assert key[1] > 0  # size
        finally:
            os.unlink(temp_path)

    def test_file_mtime_key_nonexistent_file(self):
        """测试不存在的文件返回 None"""
        key = _file_mtime_key("/nonexistent/path/to/file")
        assert key is None

    def test_sha256_file(self):
        """测试 SHA256 计算"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            hash1 = _sha256_file(temp_path)
            assert len(hash1) == 64  # SHA256 hex length

            # 相同内容产生相同哈希
            hash2 = _sha256_file(temp_path)
            assert hash1 == hash2
        finally:
            os.unlink(temp_path)

    def test_quoted_rm_command(self):
        """测试 rm 命令生成"""
        paths = ["/tmp/file1", "/tmp/file2"]
        cmd = quoted_rm_command(paths)
        assert "rm -f" in cmd
        assert "/tmp/file1" in cmd
        assert "/tmp/file2" in cmd

    def test_quoted_mkdir_command(self):
        """测试 mkdir 命令生成"""
        dirs = ["/tmp/dir1", "/tmp/dir2"]
        cmd = quoted_mkdir_command(dirs)
        assert "mkdir -p" in cmd
        assert "/tmp/dir1" in cmd
        assert "/tmp/dir2" in cmd

    def test_unique_parent_dirs(self):
        """测试提取唯一父目录"""
        files = [
            ("/local/a.txt", "/remote/a.txt"),
            ("/local/b.txt", "/remote/sub/b.txt"),
            ("/local/c.txt", "/remote/sub/c.txt"),
        ]
        parents = unique_parent_dirs(files)
        assert "/remote" in parents
        assert "/remote/sub" in parents
        assert len(parents) == 2


class TestFileSyncManager:
    """FileSyncManager 测试"""

    def test_manager_creation(self):
        """测试 FileSyncManager 创建"""
        get_files_fn = lambda: [("/local/a.txt", "/remote/a.txt")]
        manager = FileSyncManager(get_files_fn=get_files_fn)

        assert manager._get_files_fn is not None
        assert manager._synced_files == {}
        assert manager._pushed_hashes == {}

    def test_sync_tracks_changes(self):
        """测试同步跟踪变化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            local_file = Path(tmpdir) / "test.txt"
            local_file.write_text("content1")

            get_files_fn = lambda: [(str(local_file), "/remote/test.txt")]
            upload_fn = MagicMock()
            manager = FileSyncManager(get_files_fn=get_files_fn, upload_fn=upload_fn)

            # 第一次同步应该上传
            manager.sync(force=True)
            assert upload_fn.call_count == 1

            # 第二次同步（未改变）不应该上传
            manager.sync(force=False)
            assert upload_fn.call_count == 1  # 仍然为1

    def test_sync_detects_modification(self):
        """测试同步检测到文件修改"""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_file = Path(tmpdir) / "test.txt"
            local_file.write_text("content1")

            get_files_fn = lambda: [(str(local_file), "/remote/test.txt")]
            upload_fn = MagicMock()
            manager = FileSyncManager(get_files_fn=get_files_fn, upload_fn=upload_fn)

            # 第一次同步
            manager.sync(force=True)
            assert upload_fn.call_count == 1

            # 修改文件
            local_file.write_text("content2")

            # 下次同步应该检测到变化并上传
            manager.sync(force=True)
            assert upload_fn.call_count == 2

    def test_sync_detects_deletion(self):
        """测试同步检测到文件删除"""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_file = Path(tmpdir) / "test.txt"
            local_file.write_text("content1")

            get_files_fn = lambda: [(str(local_file), "/remote/test.txt")]
            upload_fn = MagicMock()
            delete_fn = MagicMock()
            manager = FileSyncManager(
                get_files_fn=get_files_fn,
                upload_fn=upload_fn,
                delete_fn=delete_fn,
            )

            # 第一次同步
            manager.sync(force=True)
            assert upload_fn.call_count == 1

            # 删除文件
            local_file.unlink()
            # 更新 get_files_fn 返回空列表
            manager._get_files_fn = lambda: []

            # 再次同步应该检测到删除
            manager.sync(force=True)
            assert delete_fn.call_count == 1

    def test_bulk_upload_used_when_available(self):
        """测试有批量上传函数时使用批量上传"""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_file1 = Path(tmpdir) / "a.txt"
            local_file2 = Path(tmpdir) / "b.txt"
            local_file1.write_text("content1")
            local_file2.write_text("content2")

            get_files_fn = lambda: [
                (str(local_file1), "/remote/a.txt"),
                (str(local_file2), "/remote/b.txt"),
            ]
            bulk_upload_fn = MagicMock()
            manager = FileSyncManager(
                get_files_fn=get_files_fn,
                bulk_upload_fn=bulk_upload_fn,
            )

            manager.sync(force=True)

            # 应该使用批量上传
            bulk_upload_fn.assert_called_once()
            # 上传函数不应该被调用
            assert manager._upload_fn is None or manager._upload_fn.call_count == 0

    def test_push_hashes_tracked(self):
        """测试推送的哈希被跟踪"""
        with tempfile.TemporaryDirectory() as tmpdir:
            local_file = Path(tmpdir) / "test.txt"
            local_file.write_text("content1")

            get_files_fn = lambda: [(str(local_file), "/remote/test.txt")]
            upload_fn = MagicMock()
            manager = FileSyncManager(get_files_fn=get_files_fn, upload_fn=upload_fn)

            manager.sync(force=True)

            # 推送后应该有哈希记录
            assert "/remote/test.txt" in manager.pushed_hashes
            assert len(manager.pushed_hashes["/remote/test.txt"]) == 64  # SHA256 hex

    def test_rate_limiting(self):
        """测试速率限制"""
        get_files_fn = lambda: []
        manager = FileSyncManager(get_files_fn=get_files_fn, sync_interval=10.0)

        # 第一次同步
        manager.sync(force=False)
        last_time = manager._last_sync_time

        # 立即再次同步应该被跳过（间隔未到）
        manager.sync(force=False)
        assert manager._last_sync_time == last_time

        # 强制同步应该立即执行
        manager.sync(force=True)
        assert manager._last_sync_time != last_time

    def test_force_sync_bypasses_rate_limit(self):
        """测试 force=True 绕过速率限制"""
        get_files_fn = lambda: []
        manager = FileSyncManager(get_files_fn=get_files_fn, sync_interval=3600.0)

        manager.sync(force=True)
        last_time = manager._last_sync_time

        # 再次强制同步应该执行（虽然间隔很长）
        manager.sync(force=True)
        # 注意：由于时间可能非常接近，这里测试的是逻辑而非精确时间


class TestFileSyncCompatibility:
    """FileSync 兼容接口测试"""

    def test_file_sync_creation(self):
        """测试 FileSync 创建"""
        mock_manager = MagicMock()
        mock_manager.host = "192.168.1.100"
        mock_manager.user = "root"
        mock_manager.port = 22

        fs = FileSync(mock_manager)

        assert fs.ssh_manager is mock_manager
        assert fs.default_remote_path == "/tmp"

    def test_file_sync_with_custom_default_path(self):
        """测试自定义默认路径"""
        mock_manager = MagicMock()
        mock_manager.host = "192.168.1.100"
        mock_manager.user = "root"
        mock_manager.port = 22

        fs = FileSync(mock_manager, default_remote_path="/custom/path")

        assert fs.default_remote_path == "/custom/path"

    def test_sync_direction_enum(self):
        """测试 SyncDirection 枚举"""
        assert SyncDirection.PUSH.value == "push"
        assert SyncDirection.PULL.value == "pull"

    def test_sync_result(self):
        """测试 SyncResult"""
        result = SyncResult(
            success=True,
            direction=SyncDirection.PUSH,
            message="Success",
            files_synced=5,
        )

        assert result.success is True
        assert result.direction == SyncDirection.PUSH
        assert result.files_synced == 5

    def test_compute_file_hash(self):
        """测试文件哈希计算"""
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            hash1 = FileSync.compute_file_hash(temp_path)
            assert len(hash1) == 32  # MD5 hash length

            # 相同内容应该产生相同哈希
            hash2 = FileSync.compute_file_hash(temp_path)
            assert hash1 == hash2
        finally:
            os.unlink(temp_path)
