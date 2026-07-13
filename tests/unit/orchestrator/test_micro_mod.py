"""U5: micro-modification 节点单测。

覆盖:
- target_files 路径正确解析(绝对 + 相对 operator_dir)
- file_write 命中 target → 替换 code_result.files
- file_write 不命中 target → 追加并标记 unexpected
- 0 file_write 调用 → micro_mod_result.success=False
- skill 跟踪(skill_names 写入 registry)
- opt-in 包装(None 跳过)
"""

from __future__ import annotations

from typing import Any

from ascend_op_agent.orchestrator.cannbot_loader import SkillUsageRegistry
from ascend_op_agent.orchestrator.nodes.micro_mod import (
    make_micro_mod_node,
    make_micro_mod_node_opt_in,
)
from ascend_op_agent.orchestrator.state_machine import Node


class _FakeMem:
    def __init__(self):
        self._p = {}

    def add(self, p, c):
        self._p.setdefault(p, []).append(c)

    def get(self, p):
        return list(self._p.get(p, []))


class _FakeAgent:
    def __init__(self, tool_calls=None, response_text="k"):
        self._conversation_history = []
        self.memory = _FakeMem()
        self._tool_calls_log = list(tool_calls or [])
        self._response = response_text

    def run_conversation(
        self,
        prompt,
        skills_layer_override=None,
        *,
        task_type=None,
    ):
        return self._response


def _factory(agent):
    return lambda: agent


def _state_with_scaffold(scaffold_files):
    return {
        "code_result": {"files": scaffold_files},
        "messages": [],
        "memory_pools": {},
    }


# ---- target_files 路径解析 ----


def test_target_files_absolute_pass_through():
    agent = _FakeAgent()
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/abs/path/kernel.cpp"],
        instruction="add broadcasting",
        agent_factory=_factory(agent),
    )
    # 看 prompt 包含绝对路径
    state = _state_with_scaffold([{"path": "/abs/path/kernel.cpp", "content": "// kernel"}])
    agent._tool_calls_log = [
        {"name": "file_write", "args": {"path": "/abs/path/kernel.cpp", "content": "// mod"}}
    ]
    update = node.func(state)
    assert update["micro_mod_result"]["target_files"] == ["/abs/path/kernel.cpp"]
    assert "/abs/path/kernel.cpp" in update["micro_mod_result"]["files_changed"]


def test_target_files_relative_resolved_via_operator_dir():
    """相对路径加 operator_dir 前缀;LLM 写回相对路径 → 不在 target_set → unexpected。"""
    agent = _FakeAgent(
        tool_calls=[
            {"name": "file_write", "args": {"path": "/tmp/op/kernel.cpp", "content": "// mod"}}
        ]
    )
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["op_kernel/add_example_arch22.cpp"],  # 相对
        instruction="add broadcasting",
        agent_factory=_factory(agent),
        operator_dir="/tmp/op",
    )
    state = _state_with_scaffold(
        [{"path": "/tmp/op/op_kernel/add_example_arch22.cpp", "content": "// orig"}]
    )
    update = node.func(state)
    # 相对路径已解析为 /tmp/op/op_kernel/add_example_arch22.cpp
    assert update["micro_mod_result"]["target_files"] == [
        "/tmp/op/op_kernel/add_example_arch22.cpp"
    ]
    # LLM 写的是 /tmp/op/kernel.cpp(不是 target),算 unexpected
    assert "/tmp/op/kernel.cpp" in update["micro_mod_result"]["files_added_unexpected"]


# ---- file_write 处理 ----


def test_file_write_in_target_replaces_existing_file():
    agent = _FakeAgent(
        tool_calls=[
            {"name": "file_write", "args": {"path": "/tmp/op/kernel.cpp", "content": "// new"}}
        ]
    )
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/kernel.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
    )
    state = _state_with_scaffold(
        [{"path": "/tmp/op/kernel.cpp", "content": "// old", "tool": "scaffold_loaded"}]
    )
    update = node.func(state)
    files = update["code_result"]["files"]
    assert len(files) == 1
    assert files[0]["content"] == "// new"  # 替换
    assert files[0]["path"] == "/tmp/op/kernel.cpp"
    assert files[0]["tool"] == "micro_mod"  # tool 字段更新
    assert update["micro_mod_result"]["success"] is True
    assert update["micro_mod_result"]["files_changed"] == ["/tmp/op/kernel.cpp"]


def test_file_write_outside_target_marks_unexpected():
    agent = _FakeAgent(
        tool_calls=[{"name": "file_write", "args": {"path": "/tmp/op/random.txt", "content": "x"}}]
    )
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/kernel.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
    )
    state = _state_with_scaffold([{"path": "/tmp/op/kernel.cpp", "content": "// orig"}])
    update = node.func(state)
    # 失败:没改 target
    assert update["micro_mod_result"]["success"] is False
    assert update["micro_mod_result"]["files_changed"] == []
    # 但意外的文件被追加 + 标记
    assert "/tmp/op/random.txt" in update["micro_mod_result"]["files_added_unexpected"]


def test_no_file_write_calls_returns_success_false():
    agent = _FakeAgent(tool_calls=[])  # LLM 没调 file_write
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/kernel.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
    )
    state = _state_with_scaffold([{"path": "/tmp/op/kernel.cpp", "content": "// orig"}])
    update = node.func(state)
    assert update["micro_mod_result"]["success"] is False
    assert update["micro_mod_result"]["files_changed"] == []
    assert update["micro_mod_result"]["files_added_unexpected"] == []


# ---- 多文件 ----


def test_multi_file_target_writes_replaces_each():
    agent = _FakeAgent(
        tool_calls=[
            {"name": "file_write", "args": {"path": "/tmp/op/kernel.cpp", "content": "// k"}},
            {"name": "file_write", "args": {"path": "/tmp/op/host.cpp", "content": "// h"}},
        ]
    )
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/kernel.cpp", "/tmp/op/host.cpp"],
        instruction="add broadcasting",
        agent_factory=_factory(agent),
    )
    state = _state_with_scaffold(
        [
            {"path": "/tmp/op/kernel.cpp", "content": "// orig-k"},
            {"path": "/tmp/op/host.cpp", "content": "// orig-h"},
        ]
    )
    update = node.func(state)
    assert update["micro_mod_result"]["success"] is True
    assert len(update["micro_mod_result"]["files_changed"]) == 2
    files = {f["path"]: f["content"] for f in update["code_result"]["files"]}
    assert files["/tmp/op/kernel.cpp"] == "// k"
    assert files["/tmp/op/host.cpp"] == "// h"


# ---- P0-2 共存契约 ----


def test_micro_mod_preserves_messages_and_memory_pools():
    agent = _FakeAgent(tool_calls=[])
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/x.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
    )
    state = _state_with_scaffold([])
    state["messages"] = [{"role": "user", "content": "prior user msg"}]
    state["memory_pools"] = {"memory": ["ctx1", "ctx2"]}
    update = node.func(state)
    # messages:本节点 set 单条 assistant(同 make_llm_node 行为)。
    # memory_pools 保留(因为 _node 显式 dict(existing_memory) 拷贝再更新)。
    assert update["messages"] == [{"role": "assistant", "content": "k"}]
    assert update["memory_pools"]["memory"] == ["ctx1", "ctx2"]


# ---- skill 跟踪(U2 集成)----


def test_micro_mod_writes_skill_to_registry():
    SkillUsageRegistry.instance().clear("t-mm")
    agent = _FakeAgent(tool_calls=[])
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/x.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
        skill_names=["npu-arch"],
    )
    state = _state_with_scaffold([])
    state["thread_id"] = "t-mm"
    node.func(state)
    loads = SkillUsageRegistry.instance().get_loads("t-mm")
    assert len(loads) == 1
    assert loads[0].phase == "micro_mod"
    assert loads[0].skill_names == ["npu-arch"]
    SkillUsageRegistry.instance().clear("t-mm")


def test_micro_mod_no_skill_names_no_tracking():
    SkillUsageRegistry.instance().clear("t-no")
    agent = _FakeAgent(tool_calls=[])
    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/x.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
        skill_names=None,
    )
    state = _state_with_scaffold([])
    state["thread_id"] = "t-no"
    update = node.func(state)
    assert "skill_loads" not in update
    assert SkillUsageRegistry.instance().get_loads("t-no") == []


# ---- opt-in 包装 ----


def test_opt_in_disabled_returns_none():
    agent = _FakeAgent()
    node = make_micro_mod_node_opt_in(
        phase="micro_mod",
        target_files=["/tmp/op/x.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
        enabled=False,
    )
    assert node is None


def test_opt_in_enabled_returns_node():
    agent = _FakeAgent()
    node = make_micro_mod_node_opt_in(
        phase="micro_mod",
        target_files=["/tmp/op/x.cpp"],
        instruction="x",
        agent_factory=_factory(agent),
        enabled=True,
    )
    assert isinstance(node, Node)
