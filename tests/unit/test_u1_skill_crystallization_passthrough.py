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

"""U1: TaskRouter task_type 传透链路 + list_cannbot_skill_names helper(PR-A scope 收缩版)。

覆盖 scenarios(plan U1.Test scenarios):
- Happy: ``PhaseRunner.invoke(task_type="develop")`` leaves ``state["task_type"] == "develop"``
- Happy: ``_dispatch_develop`` end-to-end sets it(thread state 落到 checkpoint)
- Happy: 下游节点 ``state.get("task_type") == "develop"``(透传给 LLM 节点)
- Happy: ``AIAgent.run_conversation(user_input, task_type="develop")`` 存
  ``self._current_task_type = "develop"`` 并经 ``build_system_prompt(task_type=...)``
  透传给 PromptBuilder.Layer 6
- Happy: ``list_cannbot_skill_names(CANNBOT_ROOT)`` 返回非空 set
- Happy: 二次调用 ``lru_cache`` 命中(``cache_info().hits > 0``)
- Edge:  vendored cannbot 缺失 → ``list_cannbot_skill_names`` 返回空 set, 不抛
- Edge:  ``PromptBuilder._build_skills_layer(override=..., task_type=...)`` 在 U1
  仍以 override 短路(向后兼容 hybrid,U2 重写合并渲染)
- Error: ``dispatch_develop`` 不为 develop 抛 ``TaskGatedError``;migrate/analyze/
  optimize 仍 gated

U1 不动 migrate/analyze/optimize stub executor(PR-A scope 收缩 → U8/U9)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ascend_op_agent.agent.core import AIAgent
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.orchestrator.cannbot_loader import (
    CANNBOT_ROOT,
    SKILL_BUNDLES,
    _list_cannbot_skill_names_default,
    list_cannbot_skill_names,
)
from ascend_op_agent.orchestrator.checkpoint import CheckpointStore
from ascend_op_agent.orchestrator.state import initial_state
from ascend_op_agent.orchestrator.state_machine import Node, PhaseRunner
from ascend_op_agent.task_router import TaskExecutorUnavailable, TaskGatedError, TaskRouter
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
    TaskStore,
)


# ---- Fixtures & helpers ----


@pytest.fixture
def store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


@pytest.fixture
def cannbot_available() -> bool:
    """检查 vendored cannbot submodule 是否初始化。"""
    return (CANNBOT_ROOT / "ops-lab" / "cuda2ascend-simt" / "SKILL.md").is_file()


# ---- PhaseRunner.invoke 接受 task_type 并写入 state ----


class _RecorderNode:
    """记录 state 快照的最小 node,用于验证 invoke 写入的字段能传到下游节点。"""

    def __init__(self, name: str = "recorder") -> None:
        self.name = name
        self.last_state: dict | None = None

    def __call__(self, state: dict) -> dict:
        self.last_state = dict(state)
        return {"__status__": "done"}


def test_phase_runner_invoke_writes_task_type_to_state(tmp_path) -> None:
    """invoke(task_type='develop') 后 state['task_type'] == 'develop'。"""
    store = CheckpointStore(tmp_path / "ck.db")
    recorder = _RecorderNode()
    runner = PhaseRunner(nodes=[Node(name="r", func=recorder)], store=store)

    state = runner.invoke("design add op", thread_id="t1", task_type="develop")

    assert state["task_type"] == "develop"
    # recorder 看到的 state(节点入口)也应含 task_type —— 验证 reducer 内传递
    assert recorder.last_state is not None
    assert recorder.last_state.get("task_type") == "develop"
    # task_type 不写入 messages,仍只是 user_input 一条
    assert len(state["messages"]) == 1
    assert state["messages"][0]["role"] == "user"


def test_phase_runner_invoke_default_task_type_is_none(tmp_path) -> None:
    """invoke(...) 不传 task_type → state 不含 task_type 键(state.get 返回 None)。

    关键(U2 默认路径前置):非 PhaseRunner 路径(如纯 /learn 聊天)默认 task_type=None,
    Layer 6 走"只 self-built, 不渲染 cannbot 全量"。
    """
    store = CheckpointStore(tmp_path / "ck.db")
    recorder = _RecorderNode()
    runner = PhaseRunner(nodes=[Node(name="r", func=recorder)], store=store)

    state = runner.invoke("hello", thread_id="t1")

    # U1 契约:state.get('task_type') == None(未设置,不返回空串)
    assert state.get("task_type") is None
    # initial_state 也显式写 task_type=None
    blank = initial_state("blank")
    assert blank.get("task_type") is None


def test_phase_runner_invoke_records_each_task_type(tmp_path) -> None:
    """不同 task_type 写入对应字段(可与 TASK_TYPES 常量配对)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    recorder = _RecorderNode()
    runner = PhaseRunner(nodes=[Node(name="r", func=recorder)], store=store)

    for ttype in (TASK_TYPE_DEVELOP, "migrate", "analyze", "optimize"):
        state = runner.invoke("x", thread_id=f"t-{ttype}", task_type=ttype)
        assert state["task_type"] == ttype, f"task_type mismatch for {ttype}"


# ---- TaskRouter._dispatch_develop 端到端:develop dispatch 后 thread state 含 task_type ----


class _CapturingOrchestrator:
    """模拟 PhaseRunner:记录 invoke kwargs,返回 state 含 task_type 字段透传。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, user_input, thread_id, task_type=None):
        self.calls.append(
            {
                "user_input": user_input,
                "thread_id": thread_id,
                "task_type": task_type,
            }
        )
        return {
            "current_phase": "codegen",
            "thread_id": thread_id,
            "task_type": task_type,
        }


def test_dispatch_develop_passes_task_type_to_orchestrator(store) -> None:
    """_dispatch_develop(task_type=...) 把 task.type 通过 invoke keyword 传入。"""
    orch = _CapturingOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(TASK_TYPE_DEVELOP, {"op": "add"})
    result = router.dispatch(tid, "开发 add 算子")

    assert len(orch.calls) == 1
    call = orch.calls[0]
    assert call["task_type"] == TASK_TYPE_DEVELOP
    assert call["user_input"] == "开发 add 算子"
    # state 也回传 task_type(PhaseRunner.invoke 写入,本测试 orchestrator 模拟也照做)
    assert result["state"]["task_type"] == TASK_TYPE_DEVELOP


@pytest.mark.parametrize("gated_type", [TASK_TYPE_MIGRATE, TASK_TYPE_ANALYZE, TASK_TYPE_OPTIMIZE])
def test_dispatch_gated_types_still_raise(store, gated_type) -> None:
    """migrate/analyze/optimize dispatch 仍 raise TaskGatedError(U1 scope: 不动 stub)。"""
    orch = _CapturingOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(gated_type)
    with pytest.raises(TaskGatedError, match="gated"):
        router.dispatch(tid, "x")
    assert orch.calls == []  # 无 invoke 副作用


def test_dispatch_develop_does_not_raise_taskgatederror(store) -> None:
    """develop task 不抛 TaskGatedError(F3 已有契约保持:U1 不动)。"""
    orch = _CapturingOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    # 显式不抛
    result = router.dispatch(tid, "x")
    assert "thread_id" in result
    assert "state" in result


# ---- 下游 LLM 节点从 state 读 task_type 并透传 run_conversation ----


class _TaskTypeRecorderAgent:
    """Mock AIAgent:记录 run_conversation kwargs,模拟 P0-2 共存契约所需接口。"""

    def __init__(self) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = _MemoryStub()
        self.run_calls: list[dict[str, Any]] = []

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override=None,
        *,
        task_type=None,
    ):
        self.run_calls.append(
            {
                "user_input": user_input,
                "skills_layer_override": skills_layer_override,
                "task_type": task_type,
            }
        )
        self._conversation_history.append({"role": "user", "content": user_input})
        response = "ok"
        self._conversation_history.append({"role": "assistant", "content": response})
        return response


class _MemoryStub:
    def add(self, pool: str, content: str) -> None:  # noqa: D401
        pass

    def get(self, pool: str) -> list[str]:
        return []


def test_llm_node_passes_task_type_to_run_conversation(tmp_path) -> None:
    """make_llm_node 节点读取 state['task_type'] → 传给 run_conversation(task_type=...)。

    验证 task_type 链路在 Orchestrator → LLM 节点 → AIAgent.run_conversation 这一段。
    """
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    captured: list[dict[str, Any]] = []
    agent = _TaskTypeRecorderAgent()
    agent.run_calls = captured  # 直接复用引用

    node = make_llm_node(
        phase="analyze",
        task_prompt_template="do {user_input}",
        agent_factory=lambda: agent,
    )
    state = {
        "thread_id": "t1",
        "messages": [{"role": "user", "content": "build add"}],
        "memory_pools": {},
        "code_result": None,
        "task_type": "develop",  # 模拟 PhaseRunner.invoke 写入
    }
    node.func(state)

    assert len(captured) == 1
    assert captured[0]["task_type"] == "develop"
    assert captured[0]["skills_layer_override"] is None


def test_llm_node_passes_task_type_none_when_state_missing(tmp_path) -> None:
    """state 没有 task_type 时,run_conversation 收 task_type=None(默认路径)。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    captured: list[dict[str, Any]] = []
    agent = _TaskTypeRecorderAgent()
    captured = agent.run_calls

    node = make_llm_node(
        phase="analyze",
        task_prompt_template="do {user_input}",
        agent_factory=lambda: agent,
    )
    state = {
        "thread_id": "t1",
        "messages": [{"role": "user", "content": "x"}],
        "memory_pools": {},
        "code_result": None,
        # 没有 task_type 字段
    }
    node.func(state)

    assert len(captured) == 1
    assert captured[0]["task_type"] is None


def test_micro_mod_node_passes_task_type_to_run_conversation(tmp_path) -> None:
    """make_micro_mod_node 同样把 state['task_type'] 传给 run_conversation。"""
    from ascend_op_agent.orchestrator.nodes.micro_mod import make_micro_mod_node

    captured: list[dict[str, Any]] = []
    agent = _TaskTypeRecorderAgent()
    captured = agent.run_calls

    node = make_micro_mod_node(
        phase="micro_mod",
        target_files=["/tmp/op/op_kernel.cpp"],
        instruction="add broadcasting",
        agent_factory=lambda: agent,
    )
    state = {
        "thread_id": "t1",
        "messages": [{"role": "user", "content": "micro modify"}],
        "memory_pools": {},
        "code_result": {"files": [{"path": "/tmp/op/op_kernel.cpp", "content": "stub"}]},
        "task_type": "develop",
    }
    node.func(state)

    assert len(captured) == 1
    assert captured[0]["task_type"] == "develop"


# ---- AIAgent.run_conversation 签名 + task_type 存储 + build_system_prompt 透传 ----


class _RecorderPromptBuilder:
    """记录 ``build_system_prompt`` kwargs 的 mock。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build_system_prompt(
        self,
        workspace_path,
        memory_store,
        skills_layer_override=None,
        task_type=None,
    ):
        self.calls.append(
            {
                "workspace_path": workspace_path,
                "memory_store": memory_store,
                "skills_layer_override": skills_layer_override,
                "task_type": task_type,
            }
        )
        return "RECORDER SYSTEM PROMPT"


def _fake_config_for_agent() -> Any:
    """构造最小 AIAgent 所需的 config stub(避免触发真实 LLM / config 加载)。"""

    class _LocalCfg:
        workspace = "/tmp"

    class _LLMCfg:
        provider = "openai"
        model = "fake"
        api_key = "fake"
        max_retries = 0
        timeout = 1

    class _Cfg:
        local = _LocalCfg()
        llm = _LLMCfg()

    return _Cfg()


def test_ai_agent_run_conversation_stores_task_type_and_passes_to_prompt_builder(tmp_path, monkeypatch) -> None:
    """``AIAgent.run_conversation(..., task_type='develop')``:

    1. 存 ``self._current_task_type = 'develop'``
    2. ``prompt_builder.build_system_prompt(... task_type='develop')`` 被调
    """
    from ascend_op_agent.agent import tools  # noqa: F401 触发自注册

    recorder_pb = _RecorderPromptBuilder()

    agent = AIAgent.__new__(AIAgent)
    agent.config = _fake_config_for_agent()
    agent.tool_registry = _FakeToolRegistry()
    agent.prompt_builder = recorder_pb
    agent.context_engine = None  # type: ignore[assignment]
    agent.memory = MemoryStore()
    agent._session_manager = None
    agent._tool_progress_callback = None
    agent._status_callback = None
    agent._llm_client = _FakeLLMClient(response="ok")
    agent._conversation_history = []
    agent._tool_calls_log = []
    agent._max_iterations = 5
    agent._current_iteration = 0
    agent._current_session_id = None
    agent._current_task_type = None

    # 配置 monkeypatch 让 _parse_tool_calls / _execute_tool_call_from_result 不报错
    # AIAgent 内部需要 _max_iterations 控制,简单返回 string 即可跑通一轮
    response = agent.run_conversation("user asks", task_type="develop")

    # 1. task_type 持久化在 self
    assert agent._current_task_type == "develop"
    # 2. build_system_prompt 收到 task_type
    assert len(recorder_pb.calls) >= 1
    for c in recorder_pb.calls:
        assert c["task_type"] == "develop"
    # 3. 不抛异常,response 是 fake llm 返回
    assert response == "ok"


def test_ai_agent_run_conversation_task_type_none_keeps_default(tmp_path) -> None:
    """``run_conversation(...)`` 不传 task_type:self._current_task_type 保持原值(None)。"""
    recorder_pb = _RecorderPromptBuilder()

    agent = AIAgent.__new__(AIAgent)
    agent.config = _fake_config_for_agent()
    agent.tool_registry = _FakeToolRegistry()
    agent.prompt_builder = recorder_pb
    agent.context_engine = None  # type: ignore[assignment]
    agent.memory = MemoryStore()
    agent._session_manager = None
    agent._tool_progress_callback = None
    agent._status_callback = None
    agent._llm_client = _FakeLLMClient(response="ok")
    agent._conversation_history = []
    agent._tool_calls_log = []
    agent._max_iterations = 5
    agent._current_iteration = 0
    agent._current_session_id = None
    agent._current_task_type = None  # 起始 None

    agent.run_conversation("hi")  # 不传 task_type

    assert agent._current_task_type is None
    assert any(c["task_type"] is None for c in recorder_pb.calls)


class _FakeToolRegistry:
    """满足 AIAgent 的最小接口(tool_registry.to_openai_format + get_tool)。"""

    def to_openai_format(self):
        return []

    def list_tools(self):
        return []


class _FakeLLMClient:
    """Stub LLMClient:返回固定 response。"""

    def __init__(self, response: str = "ok"):
        self.response = response

    def call(self, system_prompt, conversation_history, tools=None):
        return self.response


# ---- PromptBuilder._build_skills_layer override 短路(U1 向后兼容) ----


def test_prompt_builder_skills_layer_override_short_circuits(tmp_path) -> None:
    """override 非 None 时仍直接作为 Layer 6 内容(U2 衔接期不动)。

    U1 contract:override + task_type 都接受参数,但当前函数体仍以
    ``if override is not None: return override`` 短路 —— 防止 U2 接入合并渲染前
    就破坏现有 PhaseRunner hybrid 集成。
    """
    pb = PromptBuilder()
    override_text = "## Skills\n- cuda2ascend-simt: foo"
    out = pb._build_skills_layer(override=override_text, task_type="develop")
    assert out == override_text
    # task_type 在 U1 仅 plumbing,不影响默认 Layer 6 输出
    default = pb._build_skills_layer(override=None, task_type=None)
    assert "Available Skills" in default


def test_prompt_builder_default_skills_layer_is_static(tmp_path) -> None:
    """默认 Layer 6 是 static literal(U2 重写前的现状)。"""
    pb = PromptBuilder()
    out = pb._build_skills_layer()
    assert "Available Skills" in out
    assert "skill_ops" in out or "skill" in out.lower()


# ---- list_cannbot_skill_names(PR-A U1 helper) ----


def test_list_cannbot_skill_names_returns_set_of_strings(cannbot_available: bool) -> None:
    """``list_cannbot_skill_names()`` 在 vendored submodule 已初始化时返回非空 set。"""
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")

    names = list_cannbot_skill_names()

    assert isinstance(names, set)
    assert len(names) > 0
    for n in names:
        assert isinstance(n, str)
        assert n  # 非空


def test_list_cannbot_skill_names_matches_skill_bundles_known_paths(cannbot_available: bool) -> None:
    """已知 vendored skill 路径对应的 frontmatter name 都应出现。"""
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")

    names = list_cannbot_skill_names()

    # 这些是 SKILL_BUNDLES 决策表 7 phase bucket 中具体声明的 skill 名
    # (来自 cannbot 仓库 frontmatter `name` 字段,与目录名一致)
    expected_substrings = (
        "cuda2ascend-simt",
        "triton-op",
        "ascendc-tiling",
        "ascendc-simt",
        "ascendc-direct",
        "ascendc-code-review",
        "ascendc-crash",
        "ascendc-runtime",
        "ascendc-precision",
        "pypto-precision",
    )
    matched = sum(
        1 for n in names for s in expected_substrings if s in n
    )
    # 至少 6 个核心 skill 名命中(避免硬编码 10 —— 后续 cannbot 改名可放宽)
    assert matched >= 6, f"only {matched} known names in {names}"


def test_list_cannbot_skill_names_lru_cache_hit(cannbot_available: bool) -> None:
    """二次调用走 ``lru_cache`` 命中(``cache_info().hits > 0``)。"""
    if not cannbot_available:
        pytest.skip("cannbot-skills submodule not initialized")

    # 先清缓存,确保 baseline = (hits=0, misses=0)
    _list_cannbot_skill_names_default.cache_clear()
    assert _list_cannbot_skill_names_default.cache_info().hits == 0

    list_cannbot_skill_names()  # 第 1 次 → miss
    list_cannbot_skill_names()  # 第 2 次 → hit

    info = _list_cannbot_skill_names_default.cache_info()
    assert info.hits >= 1
    assert info.misses >= 1


def test_list_cannbot_skill_names_vendored_missing_returns_empty(tmp_path) -> None:
    """``root`` 指向不存在路径 → 返回空 set,不抛异常。"""
    out = list_cannbot_skill_names(root=tmp_path / "does-not-exist")
    assert out == set()


def test_list_cannbot_skill_names_vendored_missing_cannbot_root(monkeypatch) -> None:
    """``CANNBOT_ROOT`` 不存在时(默认 root)→ 返回空 set,不抛。"""
    fake_root = Path("/nonexistent/path/that/never/exists/cannbot")
    monkeypatch.setattr(
        "ascend_op_agent.orchestrator.cannbot_loader.CANNBOT_ROOT",
        fake_root,
    )
    # 清缓存确保新 root 生效
    _list_cannbot_skill_names_default.cache_clear()

    out = list_cannbot_skill_names()
    assert out == set()


def test_list_cannbot_skill_names_explicit_root_does_not_use_cache(tmp_path, monkeypatch) -> None:
    """``list_cannbot_skill_names(root=...)`` 不走 lru_cache(允许测试隔离)。

    验证显式 root 路径在第二次调用时不被错误地从全局缓存返回。
    """
    fake_root = tmp_path / "fake-cannbot"
    fake_root.mkdir()

    # 创建伪 SKILL_BUNDLES 子目录 + SKILL.md 含 name 字段 → 应被收集
    skill_a = fake_root / "ops" / "fake-skill-a"
    skill_a.mkdir(parents=True)
    (skill_a / "SKILL.md").write_text(
        "---\nname: fake-skill-a\ndescription: testing\n---\nbody\n",
        encoding="utf-8",
    )

    # monkey-patch SKILL_BUNDLES 以含一个虚拟 skill 路径
    import ascend_op_agent.orchestrator.cannbot_loader as cl
    original_bundles = cl.SKILL_BUNDLES
    monkeypatch.setattr(
        cl,
        "SKILL_BUNDLES",
        {("any", "test"): ["ops/fake-skill-a"]},
    )

    # 第 1 次:扫描 → fake-skill-a 应在结果中
    first = list_cannbot_skill_names(root=fake_root)
    assert "fake-skill-a" in first

    # 第 2 次:仍扫描 fake_root(不走全局 cache)→ 也应在
    second = list_cannbot_skill_names(root=fake_root)
    assert "fake-skill-a" in second

    # reset
    monkeypatch.setattr(cl, "SKILL_BUNDLES", original_bundles)


def test_list_cannbot_skill_names_explicit_root_skips_unreadable(tmp_path, monkeypatch) -> None:
    """显式 root 下 SKILL.md 不可读 / frontmatter 缺字段 → 静默跳过。"""
    fake_root = tmp_path / "broken-cannbot"
    fake_root.mkdir()

    skill_no_name = fake_root / "ops" / "no-name"
    skill_no_name.mkdir(parents=True)
    (skill_no_name / "SKILL.md").write_text(
        "---\ndescription: missing name\n---\nbody\n",
        encoding="utf-8",
    )

    skill_broken_fm = fake_root / "ops" / "broken-fm"
    skill_broken_fm.mkdir(parents=True)
    (skill_broken_fm / "SKILL.md").write_text(
        "not yaml frontmatter at all\nbody\n",
        encoding="utf-8",
    )

    skill_missing_md = fake_root / "ops" / "no-skill-md"
    skill_missing_md.mkdir(parents=True)
    # 不创建 SKILL.md

    import ascend_op_agent.orchestrator.cannbot_loader as cl
    original_bundles = cl.SKILL_BUNDLES
    monkeypatch.setattr(
        cl,
        "SKILL_BUNDLES",
        {
            ("any", "test"): [
                "ops/no-name",
                "ops/broken-fm",
                "ops/no-skill-md",
            ]
        },
    )

    # 不抛异常,空集
    out = list_cannbot_skill_names(root=fake_root)
    assert out == set()

    monkeypatch.setattr(cl, "SKILL_BUNDLES", original_bundles)
