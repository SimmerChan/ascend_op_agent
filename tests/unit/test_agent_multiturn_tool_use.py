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

"""修复 agent 多轮 tool_use 协议:ToolCallResult 分支存 tool_use 元数据 +
AnthropicAdapter 重建 native tool_use/tool_result block。

覆盖(修复前全断,修复后全绿):
- 多轮闭环: ToolCallResult(第一轮) → 执行工具 → str(第二轮) 返回
- _conversation_history 的 tool 条目含 tool_use_id/tool_name/tool_input 元数据
- 第二轮 LLM 收到的 history 含元数据(供 adapter 重建 native block)
- AnthropicAdapter 把含元数据的 history 转成 native tool_use/tool_result block
- 普通 history(无元数据)转换不变(向后兼容)

修复前根因: core.py 把 `tool_call(name)` 字符串作 assistant content + role=tool→user 丢结构,
第二轮 LLM 模仿返回 text `tool_call(name)`,走 else 分支当最终响应 → 工具结果丢失。
"""

from __future__ import annotations

from typing import Any

from ascend_op_agent.agent.core import AIAgent
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.providers.base import ToolCallResult


# ---- helpers ----


class _RecorderPromptBuilder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_system_prompt(
        self, workspace_path, memory_store, skills_layer_override=None, task_type=None
    ):
        self.calls.append({"task_type": task_type})
        return "RECORDER SYSTEM PROMPT"


def _fake_config_for_agent() -> Any:
    class _LocalCfg:
        workspace = "/tmp"

    class _LLMCfg:
        provider = "anthropic"
        model = "fake"
        api_key = "fake"
        max_retries = 0
        timeout = 1

    class _Cfg:
        local = _LocalCfg()
        llm = _LLMCfg()

    return _Cfg()


class _FakeTool:
    def __init__(self, name: str, result: str = "tool_result") -> None:
        self.name = name
        self.result = result
        self.executed: list[dict] = []

    def execute(self, **kwargs):
        self.executed.append(kwargs)
        return self.result


class _FakeToolRegistryWithTools:
    def __init__(self, tools: dict[str, _FakeTool]) -> None:
        self._tools = tools

    def to_openai_format(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": n,
                    "description": "",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for n in self._tools
        ]

    def list_tools(self):
        return list(self._tools)

    def get_tool(self, name: str):
        return self._tools.get(name)


class _ScriptedLLMClient:
    """按脚本顺序返回 response;记录每次调用收到的 conversation_history 快照。"""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.history_snapshots: list[list[dict]] = []

    def call(self, system_prompt, conversation_history, tools=None, on_delta=None):
        self.history_snapshots.append([dict(m) for m in conversation_history])
        if not self.responses:
            return "fallback"
        return self.responses.pop(0)


def _build_agent(llm_client: _ScriptedLLMClient, tools: dict[str, _FakeTool]) -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent.config = _fake_config_for_agent()
    agent.tool_registry = _FakeToolRegistryWithTools(tools)
    agent.prompt_builder = _RecorderPromptBuilder()
    agent.context_engine = None  # type: ignore[assignment]
    agent.memory = MemoryStore()
    agent._session_manager = None
    agent._tool_progress_callback = None
    agent._status_callback = None
    agent._llm_client = llm_client
    agent._conversation_history = []
    agent._tool_calls_log = []
    agent._max_iterations = 5
    agent._current_iteration = 0
    agent._current_session_id = None
    agent._current_task_type = None
    return agent


# ---- 多轮 tool_use 闭环 ----


def test_multiturn_tool_use_loop_executes_tool_and_returns_final_text() -> None:
    """ToolCallResult(第一轮) → 执行工具 → str(第二轮) 返回。

    修复前: 第二轮 LLM 收到 `tool_call(name)` 字符串 history,模仿返回 text
    `tool_call(name)`,agent 当最终响应返回,工具结果丢失。
    """
    file_search = _FakeTool("file_search", result="found build.sh")
    llm = _ScriptedLLMClient(
        [
            ToolCallResult(
                tool_call_id="toolu_1",
                tool_name="file_search",
                arguments={"pattern": "**/build.sh"},
                raw_response=None,
            ),
            "已找到 build.sh,skill 已创建",
        ]
    )
    agent = _build_agent(llm, {"file_search": file_search})

    response = agent.run_conversation("find build.sh")

    # 最终返回第二轮 str(不是 tool_call(name) 字符串)
    assert response == "已找到 build.sh,skill 已创建"
    # 工具被调用一次,参数正确
    assert len(file_search.executed) == 1
    assert file_search.executed[0] == {"pattern": "**/build.sh"}
    # LLM 被调两次(两轮)
    assert len(llm.history_snapshots) == 2


def test_multiturn_history_contains_tool_use_metadata() -> None:
    """多轮后 _conversation_history 的 tool 条目含 tool_use_id/tool_name/tool_input。"""
    file_search = _FakeTool("file_search", result="found")
    llm = _ScriptedLLMClient(
        [
            ToolCallResult(
                tool_call_id="toolu_1",
                tool_name="file_search",
                arguments={"pattern": "x"},
                raw_response=None,
            ),
            "done",
        ]
    )
    agent = _build_agent(llm, {"file_search": file_search})

    agent.run_conversation("find x")

    history = agent._conversation_history
    # assistant tool_use 条目含元数据
    asst_tool = next(m for m in history if m["role"] == "assistant" and "tool_use_id" in m)
    assert asst_tool["tool_use_id"] == "toolu_1"
    assert asst_tool["tool_name"] == "file_search"
    assert asst_tool["tool_input"] == {"pattern": "x"}
    assert asst_tool["content"] == "tool_call(file_search)"  # str 兼容旧读取者
    # tool 结果条目含 tool_use_id
    tool_msg = next(m for m in history if m["role"] == "tool")
    assert tool_msg["tool_use_id"] == "toolu_1"
    assert tool_msg["content"] == "found"


def test_second_turn_llm_receives_metadata_history() -> None:
    """第二轮 LLM 收到的 history 含 tool_use 元数据(供 adapter 重建 native block)。"""
    file_search = _FakeTool("file_search", result="found")
    llm = _ScriptedLLMClient(
        [
            ToolCallResult(
                tool_call_id="toolu_1",
                tool_name="file_search",
                arguments={"pattern": "x"},
                raw_response=None,
            ),
            "done",
        ]
    )
    agent = _build_agent(llm, {"file_search": file_search})

    agent.run_conversation("find x")

    second_call = llm.history_snapshots[1]
    # 第二轮 history 含 user + assistant(tool_use 元数据) + tool(结果)
    assert any(m["role"] == "user" and m["content"] == "find x" for m in second_call)
    asst = next(m for m in second_call if m["role"] == "assistant" and "tool_use_id" in m)
    assert asst["tool_use_id"] == "toolu_1"
    tool = next(m for m in second_call if m["role"] == "tool")
    assert tool["tool_use_id"] == "toolu_1"


def test_three_turn_learn_scenario_writes_skill() -> None:
    """3 轮 /learn 场景: file_search → skill_manage(create) → text。"""
    file_search = _FakeTool("file_search", result="found cannbot docs")
    skill_manage = _FakeTool("skill_manage", result='{"success": true}')
    llm = _ScriptedLLMClient(
        [
            ToolCallResult(
                tool_call_id="toolu_fs",
                tool_name="file_search",
                arguments={"pattern": "**/build.sh"},
                raw_response=None,
            ),
            ToolCallResult(
                tool_call_id="toolu_sm",
                tool_name="skill_manage",
                arguments={"action": "create", "name": "cann_compile_pitfall"},
                raw_response=None,
            ),
            "skill cann_compile_pitfall 已沉淀",
        ]
    )
    agent = _build_agent(llm, {"file_search": file_search, "skill_manage": skill_manage})

    response = agent.run_conversation("/learn cann_compile 踩坑")

    assert response == "skill cann_compile_pitfall 已沉淀"
    assert len(file_search.executed) == 1
    assert len(skill_manage.executed) == 1
    assert skill_manage.executed[0]["name"] == "cann_compile_pitfall"
    # 3 轮 LLM 调用
    assert len(llm.history_snapshots) == 3


# ---- AnthropicAdapter history 转换 ----


def _make_adapter_with_mock_client(captured_messages: list) -> Any:
    from ascend_op_agent.agent.providers.anthropic_adapter import AnthropicAdapter

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.api_key = "fake"
    adapter.api_base = None
    adapter.model = "fake"
    adapter.max_retries = 1
    adapter.timeout = 1
    adapter._client = None

    class _TextBlock:
        type = "text"
        text = "ok"

    class _Resp:
        content = [_TextBlock()]

    class _StreamCM:
        """messages.stream() context manager mock:get_final_message 返 _Resp。"""

        text_stream = iter([])  # on_delta=None 不遍历

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return _Resp()

    class _Messages:
        def create(self, **kwargs):
            captured_messages.append(kwargs.get("messages"))
            return _Resp()

        def stream(self, **kwargs):
            captured_messages.append(kwargs.get("messages"))
            return _StreamCM()

    class _Client:
        messages = _Messages()

    adapter._client = _Client()
    return adapter


def test_anthropic_adapter_rebuilds_native_tool_use_block() -> None:
    """含 tool_use 元数据的 history → AnthropicAdapter 转 native tool_use/tool_result block。"""
    captured: list = []
    adapter = _make_adapter_with_mock_client(captured)

    history = [
        {"role": "user", "content": "find files"},
        {
            "role": "assistant",
            "content": "tool_call(file_search)",
            "tool_use_id": "toolu_1",
            "tool_name": "file_search",
            "tool_input": {"pattern": "**/build.sh"},
        },
        {"role": "tool", "content": "found build.sh", "tool_use_id": "toolu_1"},
    ]

    adapter.complete("system", history, tools=None)

    msgs = captured[0]
    # user → assistant(tool_use block) → user(tool_result block)
    assert len(msgs) == 3
    assert msgs[0] == {"role": "user", "content": "find files"}

    assert msgs[1]["role"] == "assistant"
    assert isinstance(msgs[1]["content"], list)
    block = msgs[1]["content"][0]
    assert block["type"] == "tool_use"
    assert block["id"] == "toolu_1"
    assert block["name"] == "file_search"
    assert block["input"] == {"pattern": "**/build.sh"}

    assert msgs[2]["role"] == "user"
    assert isinstance(msgs[2]["content"], list)
    rblock = msgs[2]["content"][0]
    assert rblock["type"] == "tool_result"
    assert rblock["tool_use_id"] == "toolu_1"
    assert rblock["content"] == "found build.sh"


def test_anthropic_adapter_plain_history_unchanged() -> None:
    """普通 history(无 tool_use 元数据)转换不变(向后兼容)。"""
    captured: list = []
    adapter = _make_adapter_with_mock_client(captured)

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]

    adapter.complete("system", history, tools=None)

    msgs = captured[0]
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]


def test_anthropic_adapter_role_tool_without_metadata_falls_back_to_user() -> None:
    """旧格式 role=tool 条目(无 tool_use_id,如 XML 分支/旧 history)→ 降级 user + str content。"""
    captured: list = []
    adapter = _make_adapter_with_mock_client(captured)

    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "tool_call(file_search)"},  # 无 tool_use_id
        {"role": "tool", "content": "result"},  # 无 tool_use_id
    ]

    adapter.complete("system", history, tools=None)

    msgs = captured[0]
    # assistant 无元数据 → 普通 assistant str;tool 无元数据 → 降级 user str
    assert msgs[1] == {"role": "assistant", "content": "tool_call(file_search)"}
    assert msgs[2] == {"role": "user", "content": "result"}
