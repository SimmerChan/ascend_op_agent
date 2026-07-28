"""U1:make_llm_node 同 path 覆盖单测(强化 compile fix_loop L1+L2)。

fix_loop 第 2 轮修同一文件必须生效。覆盖:
- markdown 同 path 第 2 轮新 content → 磁盘覆盖(L1)+ code_result.files 替换(L2)核心
- 首次写入不变(回归)
- 同响应多不同 path 累积不变(回归)
- file_write 分支同 path 取后者(L2 file_write 分支)
- file_write 覆盖 existing 同 path(L2 existing 替换)
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.nodes.common import make_llm_node


class _FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    """支持 file_write(_tool_calls_log)与 markdown fallback(_conversation_history)。

    make_llm_node 会把 agent._conversation_history 重置为 state.messages(:87 rehydrate)
    再调 run_conversation。真实 run_conversation 把 LLM response 作为 assistant 消息 append,
    markdown fallback 扫 assistant content。这里模拟:run_conversation append user + assistant(response)。
    """

    def __init__(
        self,
        response: str = "ok",
        tool_calls: list[dict] | None = None,
    ) -> None:
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
        # 模拟真实 LLM:response 作为 assistant 消息进 history(markdown fallback 扫这里)
        self._conversation_history.append({"role": "assistant", "content": self._response})
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


def _markdown_block(lang: str, path: str, content: str) -> str:
    """构造 ```lang \n // path \n content \n ``` 代码块(markdown fallback 期望格式)。"""
    return f"```{lang}\n// {path}\n{content}\n```"


# ---- L1+L2 核心:第 2 轮 markdown 同 path 覆盖磁盘 + files ----


def test_markdown_same_path_overwrites_disk_and_files(tmp_path):
    target = tmp_path / "op_kernel.cpp"
    target.write_text("OLD", encoding="utf-8")  # 第 1 轮已落盘的旧文件
    state = _base_state()
    state["code_result"] = {
        "files": [{"path": str(target), "content": "OLD", "tool": "markdown_block"}]
    }
    agent = _FakeAgent(response=_markdown_block("cpp", str(target), "NEW"))
    node = make_llm_node(
        phase="compile_fix_loop_fix",
        task_prompt_template="fix: {user_input}",
        agent_factory=_factory(agent),
    )
    update = node.func(state)
    files = update["code_result"]["files"]
    same = [f for f in files if f["path"] == str(target)]
    assert len(same) == 1, f"同 path 应只剩 1 条,实际 {len(same)}"  # L2
    assert same[0]["content"] == "NEW"  # L2
    assert target.read_text(encoding="utf-8") == "NEW"  # L1 磁盘覆盖


# ---- 回归:首次 markdown 写入不变 ----


def test_first_markdown_write_unchanged(tmp_path):
    target = tmp_path / "op_kernel.cpp"
    agent = _FakeAgent(response=_markdown_block("cpp", str(target), "FRESH"))
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    files = update["code_result"]["files"]
    assert len(files) == 1
    assert files[0]["content"] == "FRESH"
    assert target.read_text(encoding="utf-8") == "FRESH"


# ---- 回归:同响应多不同 path 累积 ----


def test_multiple_distinct_paths_accumulate(tmp_path):
    a = tmp_path / "a.cpp"
    b = tmp_path / "b.cpp"
    content = _markdown_block("cpp", str(a), "A") + "\n" + _markdown_block("cpp", str(b), "B")
    agent = _FakeAgent(response=content)
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    paths = {f["path"] for f in update["code_result"]["files"]}
    assert paths == {str(a), str(b)}
    assert len(update["code_result"]["files"]) == 2


# ---- L2 file_write 分支:同 path 取后者 ----


def test_file_write_same_path_takes_last():
    agent = _FakeAgent(
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/x.cpp", "content": "FIRST"},
                "result": "ok",
            },
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/x.cpp", "content": "SECOND"},
                "result": "ok",
            },
        ],
    )
    node = make_llm_node(
        phase="codegen",
        task_prompt_template="write",
        agent_factory=_factory(agent),
    )
    update = node.func(_base_state())
    files = update["code_result"]["files"]
    assert len(files) == 1, f"同 path 应只 1 条,实际 {len(files)}"
    assert files[0]["content"] == "SECOND"


# ---- L2 file_write 覆盖 existing 同 path ----


def test_file_write_overwrites_existing_same_path():
    agent = _FakeAgent(
        tool_calls=[
            {
                "name": "file_write",
                "args": {"path": "/tmp/op/x.cpp", "content": "NEW"},
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
    state["code_result"] = {"files": [{"path": "/tmp/op/x.cpp", "content": "OLD"}]}
    update = node.func(state)
    files = update["code_result"]["files"]
    assert len(files) == 1, f"existing 同 path 应被覆盖剩 1 条,实际 {len(files)}"
    assert files[0]["content"] == "NEW"
