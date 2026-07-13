"""U2: SkillUsageRegistry + signal-1 跟踪单测。

覆盖:
- SkillUsageRegistry 单例 + record_load/record_use/get_loads/clear
- extract_used_skills: file_read 路径匹配 cannbot root → skill 名(signal-1)
- make_llm_node skill_names 跟踪:跑完 LLM 写 registry + update["skill_loads"]
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ascend_op_agent.orchestrator.cannbot_loader import (
    CANNBOT_ROOT,
    SkillLoad,
    SkillUsageRegistry,
    extract_used_skills,
)
from ascend_op_agent.orchestrator.nodes.common import make_llm_node


# ---- SkillUsageRegistry 单例 ----


def test_registry_singleton_is_shared() -> None:
    r1 = SkillUsageRegistry.instance()
    r2 = SkillUsageRegistry.instance()
    assert r1 is r2


def test_registry_record_load_and_use_round_trip() -> None:
    reg = SkillUsageRegistry.instance()
    reg.clear()  # 干净起点
    reg.record_load("t1", "analyze", ["cuda2ascend-simt", "npu-arch"])
    reg.record_use("t1", "analyze", ["cuda2ascend-simt"])
    loads = reg.get_loads("t1")
    assert len(loads) == 1
    assert loads[0].phase == "analyze"
    assert loads[0].skill_names == ["cuda2ascend-simt", "npu-arch"]
    assert loads[0].used_skills == ["cuda2ascend-simt"]
    reg.clear()


def test_registry_multi_phase_per_thread() -> None:
    reg = SkillUsageRegistry.instance()
    reg.clear("t2")
    reg.record_load("t2", "analyze", ["a"])
    reg.record_use("t2", "analyze", ["a"])
    reg.record_load("t2", "design", ["b", "c"])
    reg.record_use("t2", "design", [])
    loads = reg.get_loads("t2")
    phases = [l.phase for l in loads]
    assert phases == ["analyze", "design"]  # 按 phase 字典序
    assert loads[1].skill_names == ["b", "c"]
    assert loads[1].used_skills == []
    reg.clear("t2")


def test_registry_record_use_without_load_creates_empty_names() -> None:
    reg = SkillUsageRegistry.instance()
    reg.clear("t3")
    reg.record_use("t3", "codegen", ["x"])
    loads = reg.get_loads("t3")
    assert len(loads) == 1
    assert loads[0].skill_names == []  # 未 record_load
    assert loads[0].used_skills == ["x"]
    reg.clear("t3")


def test_registry_record_load_idempotent_no_duplicate_names() -> None:
    reg = SkillUsageRegistry.instance()
    reg.clear("t4")
    reg.record_load("t4", "analyze", ["a", "b"])
    reg.record_load("t4", "analyze", ["a", "b"])  # 重复
    loads = reg.get_loads("t4")
    assert len(loads) == 1  # 不新增
    assert loads[0].skill_names == ["a", "b"]
    reg.clear("t4")


def test_registry_clear_thread_only() -> None:
    reg = SkillUsageRegistry.instance()
    reg.clear()
    reg.record_load("ta", "analyze", ["a"])
    reg.record_load("tb", "analyze", ["b"])
    reg.clear("ta")
    assert reg.get_loads("ta") == []
    assert len(reg.get_loads("tb")) == 1
    reg.clear()


def test_registry_empty_thread_or_phase_noop() -> None:
    reg = SkillUsageRegistry.instance()
    reg.record_load("", "analyze", ["a"])
    reg.record_load("t", "", ["a"])
    assert reg.get_loads("") == []


# ---- extract_used_skills (signal-1) ----


def test_extract_used_skills_matches_cannbot_path(tmp_path) -> None:
    # 构造假 cannbot root 结构
    root = tmp_path / "cannbot-skills"
    (root / "cuda2ascend-simt").mkdir(parents=True)
    (root / "triton-op-coding").mkdir()
    skill_file = root / "cuda2ascend-simt" / "SKILL.md"

    log = [{"name": "file_read", "args": {"path": str(skill_file)}}]
    used = extract_used_skills(log, cannbot_root=root)
    assert used == ["cuda2ascend-simt"]


def test_extract_used_skills_multiple_distinct_dedup(tmp_path) -> None:
    root = tmp_path / "cannbot-skills"
    (root / "cuda2ascend-simt").mkdir(parents=True)
    (root / "triton-op-coding").mkdir()
    f1 = root / "cuda2ascend-simt" / "SKILL.md"
    f2 = root / "cuda2ascend-simt" / "refs" / "x.md"  # 同 skill 不同文件
    f3 = root / "triton-op-coding" / "SKILL.md"

    log = [
        {"name": "file_read", "args": {"path": str(f1)}},
        {"name": "file_read", "args": {"path": str(f2)}},  # 同 skill 去重
        {"name": "file_read", "args": {"path": str(f3)}},
    ]
    used = extract_used_skills(log, cannbot_root=root)
    assert used == ["cuda2ascend-simt", "triton-op-coding"]


def test_extract_used_skills_ignores_non_cannbot_path(tmp_path) -> None:
    root = tmp_path / "cannbot-skills"
    (root / "cuda2ascend-simt").mkdir(parents=True)
    outside = tmp_path / "other" / "doc.md"
    outside.parent.mkdir(parents=True)

    log = [{"name": "file_read", "args": {"path": str(outside)}}]
    assert extract_used_skills(log, cannbot_root=root) == []


def test_extract_used_skills_ignores_non_file_read_tools(tmp_path) -> None:
    root = tmp_path / "cannbot-skills"
    (root / "cuda2ascend-simt").mkdir(parents=True)
    f = root / "cuda2ascend-simt" / "SKILL.md"
    log = [
        {"name": "file_write", "args": {"path": str(f)}},  # 不是 file_read
        {"name": "shell_exec", "args": {"cmd": "ls"}},
    ]
    assert extract_used_skills(log, cannbot_root=root) == []


def test_extract_used_skills_empty_log() -> None:
    assert extract_used_skills([], cannbot_root=CANNBOT_ROOT) == []


def test_extract_used_skills_handles_missing_path_arg() -> None:
    log = [
        {"name": "file_read", "args": {}},  # 无 path
        {"name": "file_read"},  # 无 args
    ]
    assert extract_used_skills(log) == []


# ---- make_llm_node skill_names 跟踪集成 ----


class _FakeMem:
    def __init__(self):
        self._p = {}
    def add(self, p, c): self._p.setdefault(p, []).append(c)
    def get(self, p): return list(self._p.get(p, []))


class _FakeAgent:
    def __init__(self, tool_calls=None):
        self._conversation_history = []
        self.memory = _FakeMem()
        self._tool_calls_log = list(tool_calls or [])

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
    ):
        return "ok"


def _factory(agent):
    return lambda: agent


def _base_state(thread_id="t-skill"):
    return {"thread_id": thread_id, "messages": [], "memory_pools": {}}


def test_make_llm_node_records_skill_load_when_skill_names_given(tmp_path) -> None:
    """skill_names 传入时,跑完 LLM 写 registry(loaded + used)。"""
    reg = SkillUsageRegistry.instance()
    reg.clear("t-rec")
    # 模拟 LLM 调了 file_read 读 cuda2ascend-simt
    root = tmp_path / "cannbot-skills"
    (root / "cuda2ascend-simt").mkdir(parents=True)
    skill_file = root / "cuda2ascend-simt" / "SKILL.md"
    agent = _FakeAgent(tool_calls=[
        {"name": "file_read", "args": {"path": str(skill_file)}}
    ])
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="x",
        agent_factory=_factory(agent),
        skill_names=["cuda2ascend-simt", "npu-arch"],
    )
    # patch extract_used_skills 用我们的 tmp root(避免依赖真实 submodule)
    with patch(
        "ascend_op_agent.orchestrator.nodes.common.extract_used_skills"
        if False else
        "ascend_op_agent.orchestrator.cannbot_loader.CANNBOT_ROOT",
        root,
    ):
        update = node.func(_base_state("t-rec"))

    # registry 记录
    loads = reg.get_loads("t-rec")
    assert len(loads) == 1
    assert loads[0].phase == "analyze"
    assert loads[0].skill_names == ["cuda2ascend-simt", "npu-arch"]
    assert loads[0].used_skills == ["cuda2ascend-simt"]
    # update 含 skill_loads 字段
    assert update["skill_loads"]["phase"] == "analyze"
    assert "cuda2ascend-simt" in update["skill_loads"]["used_skills"]
    reg.clear("t-rec")


def test_make_llm_node_no_skill_names_no_tracking() -> None:
    """skill_names=None 时不写 registry(update 不含 skill_loads)。"""
    reg = SkillUsageRegistry.instance()
    reg.clear("t-none")
    agent = _FakeAgent()
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="x",
        agent_factory=_factory(agent),
        skill_names=None,
    )
    update = node.func(_base_state("t-none"))
    assert "skill_loads" not in update
    assert reg.get_loads("t-none") == []


def test_make_llm_node_skill_tracking_does_not_break_messages() -> None:
    """skill 跟踪不破坏 P0-2 共存契约(messages / last_phase_result 仍在)。"""
    reg = SkillUsageRegistry.instance()
    reg.clear("t-coexist")
    agent = _FakeAgent()
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="do: {user_input}",
        agent_factory=_factory(agent),
        skill_names=["s1"],
    )
    update = node.func(_base_state("t-coexist"))
    assert update["messages"] == [{"role": "assistant", "content": "ok"}]
    assert update["last_phase_result"]["phase"] == "analyze"
    reg.clear("t-coexist")
