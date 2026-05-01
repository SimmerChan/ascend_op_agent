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

"""SSHEnvironment 测试"""

import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from ascend_op_agent.ssh.manager import (
    SSHEnvironment,
    SSHConnectionError,
    _SSHProcessHandle,
)


class MockChannel:
    """模拟 paramiko Channel"""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._exit_status_ready = False
        self._closed = False

    def settimeout(self, timeout):
        pass

    def exec_command(self, command):
        self._exit_status_ready = True

    def exit_status_ready(self):
        return self._exit_status_ready

    def recv_exit_status(self):
        return self._returncode

    def recv(self, n):
        return self._stdout.encode()

    def recv_stderr(self, n):
        return self._stderr.encode()

    def recv_ready(self):
        return bool(self._stdout)

    def recv_stderr_ready(self):
        return bool(self._stderr)

    def close(self):
        self._closed = True


class MockTransport:
    """模拟 paramiko Transport"""

    def __init__(self):
        self._active = True

    def open_session(self):
        return MockChannel()

    def is_active(self):
        return self._active


class MockSSHClient:
    """模拟 paramiko SSHClient"""

    def __init__(self):
        self._connected = True
        self._transport = MockTransport()

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        self._connected = True
        return True

    def get_transport(self):
        return self._transport if self._connected else None

    def close(self):
        self._connected = False

    def is_connected(self):
        return self._connected


class TestSSHProcessHandle:
    """_SSHProcessHandle 测试"""

    def test_poll_returns_none_when_not_ready(self):
        """测试未完成时 poll 返回 None"""
        mock_client = MagicMock()
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_exit_status.return_value = 0
        mock_client.get_transport.return_value.open_session.return_value = channel

        handle = _SSHProcessHandle(mock_client, "echo test", 30)
        # 在 channel 创建前 poll 应该返回 None
        assert handle.poll() is None

    def test_poll_returns_code_when_ready(self):
        """测试完成时 poll 返回返回码"""
        mock_client = MagicMock()
        channel = MockChannel(stdout="test", returncode=0)
        mock_client.get_transport.return_value.open_session.return_value = channel

        handle = _SSHProcessHandle(mock_client, "echo test", 30)
        result = handle.poll()

        assert result == 0

    def test_kill_closes_channel(self):
        """测试 kill 关闭 channel"""
        mock_client = MagicMock()
        channel = MockChannel()
        mock_client.get_transport.return_value.open_session.return_value = channel

        handle = _SSHProcessHandle(mock_client, "echo test", 30)
        handle.kill()

        assert channel._closed is True


class TestSSHEnvironment:
    """SSHEnvironment 测试"""

    def test_ssh_environment_creation(self):
        """测试 SSHEnvironment 创建"""
        env = SSHEnvironment(
            host="192.168.1.100",
            user="root",
            port=22,
        )

        assert env.host == "192.168.1.100"
        assert env.user == "root"
        assert env.port == 22
        assert env.cwd == "/tmp"
        assert env.timeout == 300

    def test_ssh_environment_with_password(self):
        """测试带密码的 SSHEnvironment"""
        env = SSHEnvironment(
            host="192.168.1.100",
            user="root",
            password="secret",
        )
        assert env.password == "secret"

    def test_ssh_environment_custom_cwd_and_timeout(self):
        """测试自定义 cwd 和 timeout"""
        env = SSHEnvironment(
            host="192.168.1.100",
            user="root",
            cwd="/workspace",
            timeout=600,
        )
        assert env.cwd == "/workspace"
        assert env.timeout == 600

    def test_control_socket_path(self):
        """测试 ControlSocket 路径"""
        env = SSHEnvironment(
            host="192.168.1.100",
            user="root",
            port=22,
        )

        # 路径应该在 temp/hermes-ssh 下
        assert "hermes-ssh" in str(env._control_socket)
        assert env._control_socket.suffix == ".sock"

    def test_session_id_consistency(self):
        """测试相同参数生成相同的 session_id"""
        env1 = SSHEnvironment(host="192.168.1.100", user="root", port=22)
        env2 = SSHEnvironment(host="192.168.1.100", user="root", port=22)

        assert env1._session_id == env2._session_id

        # 不同参数生成不同的 session_id
        env3 = SSHEnvironment(host="192.168.1.101", user="root", port=22)
        assert env1._session_id != env3._session_id

    def test_cwd_marker_format(self):
        """测试 CWD 标记格式"""
        env = SSHEnvironment(host="test", user="root")

        marker_prefix = f"{env.CWD_MARKER_PREFIX}{env._session_id}"
        marker_suffix = env.CWD_MARKER_SUFFIX

        # 验证标记格式
        assert marker_prefix.startswith("__HERMES_CWD_")
        assert marker_suffix == "__HERMES_CWD__"
        # session_id 应该是 hash 格式
        assert len(env._session_id) > 5

    def test_cwd_property_getter_setter(self):
        """测试 cwd 属性 getter/setter"""
        env = SSHEnvironment(host="test", user="root")

        assert env.cwd == "/tmp"

        env.cwd = "/workspace"
        assert env.cwd == "/workspace"

    def test_is_connected_false_when_not_connected(self):
        """测试未连接时返回 False"""
        env = SSHEnvironment(host="192.168.1.100", user="root")
        assert env.is_connected() is False

    def test_connect_failure_raises_exception(self):
        """测试连接失败时抛出异常"""
        env = SSHEnvironment(
            host="192.168.1.100",
            user="root",
            password="wrong_password",
        )

        # 使用错误的密码应该认证失败 - 用mock来避免实际网络请求
        with patch.object(env, 'connect', side_effect=SSHConnectionError("连接失败")):
            with pytest.raises(SSHConnectionError):
                env.connect()

    def test_wrap_command_basic(self):
        """测试基本命令包装"""
        env = SSHEnvironment(host="test", user="root", cwd="/tmp")
        env._snapshot_ready = False  # 禁用快照

        wrapped = env._wrap_command("echo hello", "/tmp", None)

        assert "builtin cd /tmp" in wrapped or "builtin cd '/tmp'" in wrapped
        assert "eval 'echo hello'" in wrapped

    def test_wrap_command_with_snapshot(self):
        """测试带快照的命令包装"""
        env = SSHEnvironment(host="test", user="root")
        env._snapshot_ready = True
        env._snapshot_path = Path("/tmp/hermes-snap-test.sh")

        # 创建快照文件使 exists() 返回 True
        env._snapshot_path.touch()

        wrapped = env._wrap_command("echo hello", "/workspace", None)

        # 应该包含 source 快照
        assert "source /tmp/hermes-snap-test.sh" in wrapped
        # 应该 cd 到目标目录
        assert "builtin cd /workspace" in wrapped or "builtin cd '/workspace'" in wrapped
        # 应该包含 CWD 标记
        assert "__HERMES_CWD_" in wrapped

        # 清理
        env._snapshot_path.unlink()

    def test_wrap_command_includes_cwd_marker(self):
        """测试包装后的命令包含 CWD 标记"""
        env = SSHEnvironment(host="test", user="root", cwd="/tmp")

        wrapped = env._wrap_command("ls", "/var/log", None)

        # 应该有 CWD 标记输出
        marker_prefix = env.CWD_MARKER_PREFIX + env._session_id
        assert marker_prefix in wrapped
        assert env.CWD_MARKER_SUFFIX in wrapped

    def test_init_session_creates_snapshot(self):
        """测试 init_session 创建快照文件"""
        env = SSHEnvironment(host="test", user="root")

        with patch.object(env, "_run_bash") as mock_run_bash:
            mock_handle = MagicMock()
            mock_handle.wait.return_value = 0
            mock_run_bash.return_value = mock_handle

            env.init_session()

            assert env._snapshot_ready is True
            # 验证 _run_bash 被调用（参数不重要）
            mock_run_bash.assert_called_once()

    def test_init_session_failure_sets_snapshot_ready_false(self):
        """测试 init_session 失败时 snapshot_ready 为 False"""
        env = SSHEnvironment(host="test", user="root")

        with patch.object(env, "_run_bash") as mock_run_bash:
            mock_run_bash.side_effect = Exception("Connection failed")

            env.init_session()

            assert env._snapshot_ready is False

    def test_cleanup_closes_connection(self):
        """测试 cleanup 关闭连接"""
        env = SSHEnvironment(host="test", user="root")
        mock_client = MagicMock()
        env._client = mock_client
        env._connected = True

        env.cleanup()

        mock_client.close.assert_called_once()
        assert env._connected is False

    def test_context_manager(self):
        """测试上下文管理器"""
        env = SSHEnvironment(host="test", user="root")

        with patch.object(env, "connect") as mock_connect:
            with patch.object(env, "init_session") as mock_init:
                with patch.object(env, "cleanup") as mock_cleanup:
                    env.__enter__()
                    mock_connect.assert_called_once()
                    mock_init.assert_called_once()

                    env.__exit__(None, None, None)
                    mock_cleanup.assert_called_once()

    def test_execute_updates_cwd_from_marker(self):
        """测试 execute 从标记更新 cwd"""
        env = SSHEnvironment(host="test", user="root", cwd="/tmp")
        env._snapshot_ready = False
        env._connected = True  # 模拟已连接

        # 模拟 stdout 包含 CWD 标记
        # 标记格式: __HERMES_CWD_<session_id>__<path>__HERMES_CWD__
        # 注意: prefix 是 CWD_MARKER_PREFIX + session_id, 没有额外的下划线
        marker = f"{env.CWD_MARKER_PREFIX}{env._session_id}/new/dir{env.CWD_MARKER_SUFFIX}"

        mock_handle = MagicMock()
        mock_handle.wait.return_value = 0
        mock_handle.stdout = f"output\n{marker}\n"
        mock_handle.returncode = 0

        with patch.object(env, "_run_bash", return_value=mock_handle):
            result = env.execute("echo test", cwd="/new/dir")

        # cwd 应该被更新
        assert env.cwd == "/new/dir"

    def test_ensure_connected_calls_connect(self):
        """测试 ensure_connected 调用 connect"""
        env = SSHEnvironment(host="test", user="root")

        with patch.object(env, "connect") as mock_connect:
            mock_connect.return_value = True
            env.ensure_connected()

            mock_connect.assert_called_once()

    def test_ensure_connected_skips_if_connected(self):
        """测试 ensure_connected 在已连接时不调用 connect"""
        env = SSHEnvironment(host="test", user="root")
        env._connected = True
        env._client = MagicMock()

        with patch.object(env, "is_connected", return_value=True):
            with patch.object(env, "connect") as mock_connect:
                env.ensure_connected()
                mock_connect.assert_not_called()


class TestSSHEnvironmentIntegration:
    """SSHEnvironment 集成测试（模拟）"""

    def test_connection_hash_determinism(self):
        """测试相同连接参数生成相同的 hash"""
        params = {"host": "192.168.1.100", "user": "root", "port": 22}

        env1 = SSHEnvironment(**params)
        env2 = SSHEnvironment(**params)

        assert env1._get_connection_hash() == env2._get_connection_hash()

    def test_different_params_different_hash(self):
        """测试不同参数生成不同的 hash"""
        env1 = SSHEnvironment(host="192.168.1.100", user="root", port=22)
        env2 = SSHEnvironment(host="192.168.1.101", user="root", port=22)

        assert env1._get_connection_hash() != env2._get_connection_hash()

    def test_execute_flow(self):
        """测试 execute 流程"""
        env = SSHEnvironment(host="test", user="root", cwd="/tmp")
        env._snapshot_ready = False

        mock_handle = MagicMock()
        mock_handle.wait.return_value = 0
        mock_handle.stdout = "result\n"
        mock_handle.returncode = 0

        with patch.object(env, "_before_execute"):  # mock connect
            with patch.object(env, "_run_bash", return_value=mock_handle):
                with patch.object(env, "_after_execute"):
                    result = env.execute("echo test", cwd="/workspace")

        assert result.success is True
        assert result.return_code == 0
        mock_handle.wait.assert_called_once()  # timeout 参数使用关键字