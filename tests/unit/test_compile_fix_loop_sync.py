"""U2:make_real_compile_fix_loop_node sync_fn 注入单测(强化 compile fix_loop L3)。

fix_loop apply_update 后必须把本地算子工程同步到远程(910B),否则 re-compile 读旧文件。
覆盖:
- sync_fn 在 fix 后被调用(次数 + 参数)
- sync_fn 时序:compile(fail)→ fix → sync → compile(success)
- sync_fn 被调时 state.code_result 已含 fix 后的新 files(apply_update 已应用)
- sync_fn=None no-op(本地/直连场景)
"""

from __future__ import annotations

from typing import Any

from ascend_op_agent.orchestrator.nodes.validation import (
    make_real_compile_fix_loop_node,
)


# ---- mocks ----


class _FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    """run_conversation 把 response 作为 assistant 消息进 history,触发 markdown fallback。"""

    def __init__(self, response: str = "ok") -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = _FakeMemory()
        self._tool_calls_log: list[dict] = []
        self._response = response

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
        no_tools=False,
    ) -> str:
        self._conversation_history.append({"role": "user", "content": user_input})
        self._conversation_history.append({"role": "assistant", "content": self._response})
        return self._response


class _FakeExecutor:
    """鸭子类型 mock NpuExecutor.compile_to_dict,按顺序返回预设结果。"""

    def __init__(self, results: list[dict]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def compile_to_dict(self, operator_path: str) -> dict:
        self.calls.append(operator_path)
        return self._results.pop(0)


class _FakeSync:
    """callable mock,记录 operator_path 调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, operator_path: str) -> None:
        self.calls.append(operator_path)


def _base_state() -> dict:
    return {"code_result": None, "messages": [], "memory_pools": {}}


def _resolver(path: str):
    return lambda _state: path


def _ok_compile_result(path: str = "/tmp/op") -> dict:
    return {
        "success": True,
        "return_code": 0,
        "stdout": "ok",
        "stderr": "",
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


def _fail_compile_result(stderr: str = "build error", path: str = "/tmp/op") -> dict:
    return {
        "success": False,
        "return_code": 1,
        "stdout": "",
        "stderr": stderr,
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


# ---- 核心:sync_fn 在 fix 后被调用 ----


def test_sync_fn_called_after_fix_and_before_next_compile():
    """第 1 轮 compile fail → fix(markdown 修 build.sh)→ sync_fn → 第 2 轮 compile success。"""
    executor = _FakeExecutor(
        [
            _fail_compile_result("error round 1"),
            _ok_compile_result(),
        ]
    )
    sync = _FakeSync()
    op_path = "/tmp/op"
    mb = '```bash\n// /tmp/op/build.sh\ncase "$1" in\n  -j*) echo SYNC_FIX;;\nesac\n# FIXED_BUILD_SH_CONTENT_MARK\n```'
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver(op_path),
        agent_factory=lambda: _FakeAgent(response=mb),
        sync_fn=sync,
        max_rounds=3,
    )
    update = node.func(_base_state())
    # sync 仅在第 1 轮 fix 后调 1 次(第 2 轮 success 不进 fix)
    assert len(sync.calls) == 1, f"sync_fn 应被调 1 次,实际 {len(sync.calls)}"
    assert sync.calls[0] == op_path
    # compile 调 2 次
    assert len(executor.calls) == 2
    assert update["compile_fix_loop_result"]["status"] == "done"


# ---- 时序:compile(fail)→ sync → compile(success) ----


def test_sync_fn_timing_is_compile_fix_sync_compile():
    """sync 必须在 apply_update 之后、下一轮 compile 之前(否则远程读旧文件)。"""
    events: list[str] = []

    class _Exe:
        def compile_to_dict(self, op: str) -> dict:
            events.append("compile")
            if len([e for e in events if e == "compile"]) == 1:
                return _fail_compile_result()
            return _ok_compile_result()

    class _Sync:
        def __call__(self, op: str) -> None:
            events.append("sync")

    mb = '```bash\n// /tmp/op/build.sh\ncase "$1" in\n  -j*) echo TIMING_FIX;;\nesac\n```'
    node = make_real_compile_fix_loop_node(
        executor=_Exe(),
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: _FakeAgent(response=mb),
        sync_fn=_Sync(),
        max_rounds=3,
    )
    node.func(_base_state())
    assert events == ["compile", "sync", "compile"], f"时序错:{events}"


# ---- sync 时 state 已含 fix 后 files ----


def test_sync_fn_sees_state_after_fix():
    """sync_fn 被调时 state.code_result 已含新 build.sh(apply_update 已 in-place 应用)。

    _loop 内 apply_update(state, update) 改 state in-place,后续 sync_fn(operator_path)
    拿到的 state 与 _loop 持有的是同一对象。通过共享 list 引用捕获。
    """
    captured: list[dict] = []
    state_ref: list[Any] = [None]

    class _Exe:
        def __init__(self) -> None:
            self.n = 0

        def compile_to_dict(self, op: str) -> dict:
            self.n += 1
            return _fail_compile_result() if self.n == 1 else _ok_compile_result()

    class _Sync:
        def __call__(self, op: str) -> None:
            captured.append(state_ref[0])

    mb = '```bash\n// /tmp/op/build.sh\nNEW BUILD.SH\ncase "$1" in\n  -j*) echo j;;\nesac\n```'
    node = make_real_compile_fix_loop_node(
        executor=_Exe(),
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: _FakeAgent(response=mb),
        sync_fn=_Sync(),
        max_rounds=3,
    )
    state = _base_state()
    state_ref[0] = state
    node.func(state)
    assert captured, "sync_fn 应被调"
    files = (captured[0].get("code_result") or {}).get("files") or []
    paths = {f.get("path") for f in files}
    contents = {f.get("path"): f.get("content") for f in files}
    assert "/tmp/op/build.sh" in paths, f"sync 时 build.sh 应已应用,实际 {paths}"
    assert "NEW BUILD.SH" in contents["/tmp/op/build.sh"]


# ---- sync_fn=None no-op ----


def test_sync_fn_none_is_noop():
    """本地/直连场景 sync_fn=None,_loop 正常收敛不报错。"""
    executor = _FakeExecutor([_ok_compile_result()])
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: _FakeAgent(response="ok"),
        sync_fn=None,
        max_rounds=3,
    )
    update = node.func(_base_state())
    assert update["compile_fix_loop_result"]["status"] == "done"
    assert len(executor.calls) == 1
