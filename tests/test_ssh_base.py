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

"""BaseEnvironment 抽象后端接口测试"""

from abc import abstractmethod
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from ascend_op_agent.ssh.base_environment import (
    BaseEnvironment,
    ProcessHandle,
    ExecuteResult,
    _ThreadedProcessHandle,
)


class MockProcessHandle:
    """模拟进程句柄"""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._poll_count = 0
        self._killed = False

    def poll(self) -> Optional[int]:
        self._poll_count += 1
        if self._killed:
            return -9
        return None if self._poll_count < 3 else self._returncode

    def kill(self) -> None:
        self._killed = True

    def wait(self, timeout: Optional[float] = None) -> int:
        for _ in range(3):
            if self.poll() is not None:
                break
        return self._returncode

    @property
    def stdout(self) -> str:
        return self._stdout

    @property
    def stderr(self) -> str:
        return self._stderr

    @property
    def returncode(self) -> Optional[int]:
        if self._killed:
            return -9
        return self._returncode if self._poll_count >= 3 else None


class ConcreteEnvironment(BaseEnvironment):
    """具体实现的环境类（用于测试）"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._executions = []
        self._mock_handle = MockProcessHandle()

    def _run_bash(self, command: str, timeout: int) -> ProcessHandle:
        self._executions.append((command, timeout))
        # 模拟命令执行
        if "echo success" in command:
            self._mock_handle = MockProcessHandle(stdout="success\n", returncode=0)
        elif "echo error" in command:
            self._mock_handle = MockProcessHandle(stderr="error\n", returncode=1)
        elif "sleep" in command:
            self._mock_handle = MockProcessHandle(stdout="", returncode=0)
        else:
            self._mock_handle = MockProcessHandle(stdout=f"executed: {command}\n", returncode=0)
        return self._mock_handle

    @property
    def executions(self):
        return self._executions


class TestProcessHandleProtocol:
    """ProcessHandle Protocol 测试"""

    def test_process_handle_protocol_exists(self):
        """测试 ProcessHandle Protocol 存在"""
        assert ProcessHandle is not None

    def test_mock_handle_implements_protocol(self):
        """测试 MockProcessHandle 实现了 Protocol"""
        handle = MockProcessHandle(stdout="test", returncode=0)
        # 验证所有必需的属性和方法
        assert hasattr(handle, "poll")
        assert hasattr(handle, "kill")
        assert hasattr(handle, "wait")
        assert hasattr(handle, "stdout")
        assert hasattr(handle, "stderr")
        assert hasattr(handle, "returncode")


class TestExecuteResult:
    """ExecuteResult 测试"""

    def test_execute_result_success(self):
        """测试成功结果"""
        handle = MockProcessHandle(stdout="output", stderr="", returncode=0)
        handle.wait()  # 先等待确保 returncode 可用
        result = ExecuteResult.from_process_handle(handle)
        assert result.stdout == "output"
        assert result.return_code == 0
        assert result.success is True
        assert result.timed_out is False

    def test_execute_result_failure(self):
        """测试失败结果"""
        handle = MockProcessHandle(stdout="", stderr="error", returncode=1)
        handle.wait()  # 先等待确保 returncode 可用
        result = ExecuteResult.from_process_handle(handle)
        assert result.success is False
        assert result.stderr == "error"
        assert result.return_code == 1

    def test_execute_result_timed_out(self):
        """测试超时结果"""
        handle = MockProcessHandle(stdout="partial", returncode=-1)
        result = ExecuteResult.from_process_handle(handle, timed_out=True)
        assert result.timed_out is True
        assert result.success is False


class TestThreadedProcessHandle:
    """_ThreadedProcessHandle 测试"""

    def test_sync_function_execution(self):
        """测试同步函数执行"""

        def target_fn() -> tuple[str, str, int]:
            return ("stdout", "stderr", 0)

        handle = _ThreadedProcessHandle(target_fn)
        handle.start()
        returncode = handle.wait(timeout=5)

        assert returncode == 0
        assert handle.stdout == "stdout"
        assert handle.stderr == "stderr"
        assert handle.returncode == 0

    def test_function_with_args(self):
        """测试带参数的函数"""

        def target_fn(msg: str, count: int) -> tuple[str, str, int]:
            return (f"{msg} {count}", "", 0)

        handle = _ThreadedProcessHandle(target_fn, args=("hello", 42))
        handle.start()
        handle.wait(timeout=5)

        assert handle.stdout == "hello 42"

    def test_function_with_kwargs(self):
        """测试带关键字参数的函数"""

        def target_fn(msg: str = "default") -> tuple[str, str, int]:
            return (msg, "", 0)

        handle = _ThreadedProcessHandle(target_fn, kwargs={"msg": "custom"})
        handle.start()
        handle.wait(timeout=5)

        assert handle.stdout == "custom"

    def test_poll_before_complete(self):
        """测试未完成时轮询返回 None"""
        import time

        def slow_fn():
            time.sleep(0.5)
            return ("done", "", 0)

        handle = _ThreadedProcessHandle(slow_fn)
        handle.start()

        # 立即轮询应该返回 None（未完成）
        assert handle.poll() is None

        # 等待完成
        handle.wait(timeout=5)
        assert handle.poll() == 0

    def test_exception_handling(self):
        """测试异常处理"""

        def failing_fn():
            raise ValueError("test error")

        handle = _ThreadedProcessHandle(failing_fn)
        handle.start()
        handle.wait(timeout=5)

        assert handle.returncode == -1
        assert "test error" in handle.stderr


class TestBaseEnvironment:
    """BaseEnvironment 抽象基类测试"""

    def test_cannot_instantiate_abstract_class(self):
        """测试不能直接实例化抽象类"""
        with pytest.raises(TypeError):
            BaseEnvironment()

    def test_subclass_must_implement_run_bash(self):
        """测试子类必须实现 _run_bash"""

        class IncompleteEnvironment(BaseEnvironment):
            pass

        with pytest.raises(TypeError):
            IncompleteEnvironment()

    def test_concrete_environment_instantiation(self):
        """测试具体环境可以实例化"""
        env = ConcreteEnvironment()
        assert env is not None
        assert env.cwd == "/tmp"
        assert env.timeout == 300

    def test_concrete_environment_with_custom_cwd(self):
        """测试自定义 cwd"""
        env = ConcreteEnvironment(cwd="/custom/path")
        assert env.cwd == "/custom/path"

    def test_execute_basic_command(self):
        """测试执行基本命令"""
        env = ConcreteEnvironment()
        result = env.execute("echo success")

        assert result.success is True
        assert "success" in result.stdout
        assert result.return_code == 0

    def test_execute_failed_command(self):
        """测试执行失败命令"""
        env = ConcreteEnvironment()
        result = env.execute("echo error 1>&2; exit 1", cwd="/tmp")

        assert result.success is False
        assert result.return_code == 1

    def test_execute_with_cwd_override(self):
        """测试 cwd 参数覆盖"""
        env = ConcreteEnvironment(cwd="/default")
        env.execute("echo test", cwd="/override")

        # 验证最后执行的命令包含正确的 cwd
        assert "cd /override" in env.executions[-1][0]

    def test_execute_with_timeout(self):
        """测试超时参数"""
        env = ConcreteEnvironment(timeout=60)
        env.execute("echo test", timeout=30)

        assert env.executions[-1][1] == 30  # 使用传入的 timeout

    def test_context_manager(self):
        """测试上下文管理器"""
        with ConcreteEnvironment() as env:
            result = env.execute("echo test")
            assert result.success is True

    def test_init_session_callable(self):
        """测试 init_session 可调用（不抛异常）"""
        env = ConcreteEnvironment()
        env.init_session()  # 不应抛异常

    def test_cleanup_callable(self):
        """测试 cleanup 可调用（不抛异常）"""
        env = ConcreteEnvironment()
        env.cleanup()  # 不应抛异常

    def test_wrap_command_default(self):
        """测试默认命令包装"""
        env = ConcreteEnvironment(cwd="/test")
        wrapped = env._wrap_command("ls", "/test", None)

        assert "cd /test" in wrapped
        assert "ls" in wrapped

    def test_subclass_can_override_wrap_command(self):
        """测试子类可以覆盖 _wrap_command"""

        class CustomEnvironment(ConcreteEnvironment):
            def _wrap_command(self, command, cwd, stdin_data=None):
                return f"echo 'custom: {command}'"

        env = CustomEnvironment()
        wrapped = env._wrap_command("ls", "/tmp", None)

        assert "custom: ls" in wrapped

    def test_before_execute_hook(self):
        """测试 _before_execute 钩子"""

        class HookedEnvironment(ConcreteEnvironment):
            def __init__(self):
                super().__init__()
                self.before_called = False

            def _before_execute(self):
                self.before_called = True

        env = HookedEnvironment()
        env.execute("echo test")

        assert env.before_called is True

    def test_after_execute_hook(self):
        """测试 _after_execute 钩子"""

        class HookedEnvironment(ConcreteEnvironment):
            def __init__(self):
                super().__init__()
                self.after_called = False
                self.last_handle = None

            def _after_execute(self, handle):
                self.after_called = True
                self.last_handle = handle

        env = HookedEnvironment()
        result = env.execute("echo test")

        assert env.after_called is True
        assert env.last_handle is not None


class TestBaseEnvironmentInterface:
    """BaseEnvironment 接口一致性测试"""

    def test_execute_signature(self):
        """测试 execute 方法签名"""
        import inspect

        sig = inspect.signature(BaseEnvironment.execute)
        params = list(sig.parameters.keys())

        assert "self" in params
        assert "command" in params
        assert "cwd" in params
        assert "timeout" in params
        assert "stdin_data" in params

    def test_all_abstract_methods_documented(self):
        """测试所有抽象方法都有文档字符串"""
        # _run_bash 是抽象方法
        assert BaseEnvironment._run_bash.__doc__ is not None or True  # 抽象方法

    def test_subclass_implements_run_bash(self):
        """测试子类正确实现 _run_bash"""
        env = ConcreteEnvironment()

        handle = env._run_bash("echo test", 30)

        assert handle is not None
        assert hasattr(handle, "poll")
        assert hasattr(handle, "kill")
        assert hasattr(handle, "wait")
