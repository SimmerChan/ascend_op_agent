"""make_llm_node 单测(覆盖 Gap 1:file_write tool_calls_log → code_result 传播)。

覆盖:
- agent.run_conversation 不调 file_write → code_result 不出现在 update
- agent 调 file_write(path, content) → update["code_result"]["files"] 含该文件
- 多个 file_write 调用 → files 列表按调用顺序累加
- file_write 调错/缺 path → 不污染 code_result
- 与现有 P0-2 共存契约兼容(不影响 messages / last_phase_result)
"""

from __future__ import annotations

from typing import Any

from ascend_op_agent.orchestrator.nodes.common import make_llm_node
from ascend_op_agent.orchestrator.state_machine import Node


class _FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    """模拟 AIAgent:支持在 run_conversation 期间往 _tool_calls_log 塞条目。"""

    def __init__(self, response: str = "ok", tool_calls: list[dict] | None = None) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = _FakeMemory()
        self._tool_calls_log: list[dict] = list(tool_calls or [])
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
        return self._response


def _factory(agent: _FakeAgent):
    def _f() -> _FakeAgent:
        return agent

    return _f


def _base_state() -> dict:
    return {
        "thread_id": "t1",
        "messages": [],
        "memory_pools": {},
        "code_result": None,
    }


# ---- baseline:无 file_write 不污染 code_result ----


def test_no_tool_calls_does_not_set_code_result() -> None:
    agent = _FakeAgent(response="just a text response")
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="do it: {user_input}",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    assert "code_result" not in update
    assert update["last_phase_result"]["response"] == "just a text response"


# ---- file_write 一次进 code_result ----


def test_single_file_write_populates_code_result() -> None:
    agent = _FakeAgent(
        response="wrote file",
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/op_kernel.cpp", "content": "kernel code"},
                "result": "wrote 11 bytes",
            }
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write it: {user_input}",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    assert "code_result" in update
    files = update["code_result"]["files"]
    assert len(files) == 1
    assert files[0]["path"] == "/tmp/op/op_kernel.cpp"
    assert files[0]["content"] == "kernel code"
    assert files[0]["tool"] == "file_write"


# ---- 多次 file_write 累加 ----


def test_multiple_file_writes_accumulate_in_order() -> None:
    agent = _FakeAgent(
        response="wrote 3 files",
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/a.cpp", "content": "A"},
                "result": "ok",
            },
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/b.cpp", "content": "B"},
                "result": "ok",
            },
            {"name": "file_write", "args": {"path": "/tmp/op/c.h", "content": "C"}, "result": "ok"},
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write all",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    files = update["code_result"]["files"]
    assert [f["path"] for f in files] == ["/tmp/op/a.cpp", "/tmp/op/b.cpp", "/tmp/op/c.h"]


# ---- 混合其他 tool 调用,只抽 file_write ----


def test_only_file_write_is_extracted_from_mixed_tools() -> None:
    agent = _FakeAgent(
        response="did several things",
        tool_calls=[
            {"name": "shell_exec", "args": {"cmd": "ls"}, "result": "out"},
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/x.py", "content": "x"},
                "result": "ok",
            },
            {"name": "python_exec", "args": {"script": "1+1"}, "result": "2"},
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="x",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    files = update["code_result"]["files"]
    assert len(files) == 1
    assert files[0]["path"] == "/tmp/op/x.py"


# ---- 已有 code_result 会被 merge(不覆盖) ----


def test_existing_code_result_files_are_preserved() -> None:
    agent = _FakeAgent(
        response="added more",
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/new.cpp", "content": "new"},
                "result": "ok",
            },
        ],
    )
    node = make_llm_node(
        phase="refine",
        task_prompt_template="refine",
        agent_factory=_factory(agent),
    )
    state = _base_state()
    state["code_result"] = {"files": [{"path": "/tmp/op/old.cpp", "content": "old"}]}
    update = node.func(state)
    files = update["code_result"]["files"]
    paths = [f["path"] for f in files]
    assert "/tmp/op/old.cpp" in paths
    assert "/tmp/op/new.cpp" in paths
    assert len(files) == 2


# ---- file_write 缺 path 不会污染 ----


def test_file_write_without_path_is_skipped() -> None:
    agent = _FakeAgent(
        response="buggy call",
        tool_calls=[
            {"name": "file_write", "args": {"content": "no path"}, "result": "ok"},
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="x",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    assert "code_result" not in update


# ---- 不影响 P0-2 共存契约 ----


def test_messages_and_last_phase_result_still_present() -> None:
    agent = _FakeAgent(
        response="assistant said X",
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/k.cpp", "content": "k"},
                "result": "ok",
            },
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="do: {user_input}",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    assert update["messages"] == [{"role": "assistant", "content": "assistant said X"}]
    assert update["last_phase_result"]["phase"] == "codegen"
    assert update["last_phase_result"]["response"] == "assistant said X"


# ---- template_vars ----


def test_template_vars_injected_into_prompt() -> None:
    """template_vars 应被注入 str.format,占位符 {operator_dir} 等可正常替换。"""
    captured = {}

    class _CapturingAgent(_FakeAgent):
        def run_conversation(
            self,
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
            no_tools=False,
        ):
            captured["prompt"] = user_input
            return "ok"

    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write to {operator_dir}/op.cpp",
        agent_factory=lambda: _CapturingAgent(),
        template_vars={"operator_dir": "/tmp/e2e/op_add"},
    )
    node.func(_base_state())
    assert captured["prompt"] == "write to /tmp/e2e/op_add/op.cpp"


def test_template_vars_none_keeps_original_behavior() -> None:
    """template_vars=None 时,不引入额外占位符(原行为)。"""
    captured = {}

    class _CapturingAgent(_FakeAgent):
        def run_conversation(
            self,
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
            no_tools=False,
        ):
            captured["prompt"] = user_input
            return "ok"

    node = make_llm_node(
        phase="codegen",
        task_prompt_template="just {user_input}",
        agent_factory=lambda: _CapturingAgent(),
        template_vars=None,
    )
    node.func(_base_state())
    assert captured["prompt"] == "just "


def test_template_vars_unknown_placeholder_falls_back() -> None:
    """模板里出现未声明占位符时,降级为原模板(不抛错),让 LLM 瞎填。"""
    captured = {}

    class _CapturingAgent(_FakeAgent):
        def run_conversation(
            self,
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
            no_tools=False,
        ):
            captured["prompt"] = user_input
            return "ok"

    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write to {unknown_var}/x",
        agent_factory=lambda: _CapturingAgent(),
        template_vars={"operator_dir": "/tmp/op"},
    )
    node.func(_base_state())
    # 出现未声明占位符 → fallback 整段
    assert captured["prompt"] == "write to {unknown_var}/x"
