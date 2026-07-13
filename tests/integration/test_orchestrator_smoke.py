"""PhaseRunner 集成 smoke 测试 —— U6 架构验证里程碑。

垂直切片:entry → echo_design LLM 节点 → done。验证:

1. AIAgent 被包成节点(make_llm_node + agent_factory)
2. checkpoint 持久化(每节点后落盘)
3. messages 累加(append reducer)
4. scoped skill 文本注入 PromptBuilder Layer 6(skills_layer_override)
5. 第二节点入口 AIAgent._conversation_history == state["messages"](同形校验)
6. memory_pools 跨节点持久化(rehydrate 后 Layer 5 非空)
7. HITL 中断(__interrupt__)→ status=waiting_confirm
8. 崩溃恢复(resume 从 checkpoint 续跑)

mock agent_factory 避开真实 LLM 调用;真实 wiring 在 F5/U7 落地。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from ascend_op_agent.orchestrator import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_CONFIRM,
    CheckpointStore,
    Node,
    PhaseRunner,
    make_llm_node,
)
from ascend_op_agent.orchestrator.state_machine import entry_node_factory


class FakeMemory:
    """内存 stub —— 模拟 MemoryStore 的 add/get 接口。

    用 inline stub 避开 ``agent/__init__.py`` 触发的重依赖链
    (sentence_transformers/sklearn/pandas/numpy 在 Python 3.9 环境下二进制不匹配)。
    P0-2 共存契约只要求 ``add(pool, content)`` + ``get(pool) -> list[str]``。
    """

    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class FakeAgent:
    """Mock AIAgent。

    模拟 P0-2 共存契约所需接口:
    - ``_conversation_history: list[dict]`` (与 OpState.messages 同形)
    - ``memory`` (有 add / get 方法)
    - ``run_conversation(prompt, skills_layer_override=None) -> str``
    """

    def __init__(self) -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = FakeMemory()
        self.last_skills_override: Any = None
        self.last_prompt: str = ""

    def run_conversation(
        self,
        user_input: str,
        skills_layer_override: Any = None,
        *,
        task_type: Optional[str] = None,
    ) -> str:
        self.last_prompt = user_input
        self.last_skills_override = skills_layer_override
        self.last_task_type = task_type
        # 把 user_input 作为本轮输入追加到 history(与真 agent 一致)
        self._conversation_history.append({"role": "user", "content": user_input})
        # 简单 echo:把 memory 里所有内容拼到 response
        mem_items = self.memory.get("memory")
        mem_str = "|".join(mem_items) if mem_items else "(no memory)"
        response = f"echo({user_input[:40]}) mem={mem_str}"
        self._conversation_history.append({"role": "assistant", "content": response})
        return response


def _factory_provider() -> FakeAgent:
    """每次调用返回新 FakeAgent(模拟 fresh agent per node)。"""
    return FakeAgent()


# ---- Happy path ----


def test_happy_path_runs_all_nodes_and_persists_checkpoint(tmp_path) -> None:
    """端到端:entry → echo_design → done,messages 累加 + checkpoint 落盘。"""
    store = CheckpointStore(tmp_path / "ck.db")
    created_agents: list[FakeAgent] = []

    def factory() -> FakeAgent:
        a = FakeAgent()
        created_agents.append(a)
        return a

    nodes = [
        entry_node_factory(),
        make_llm_node(
            phase="echo_design",
            task_prompt_template="design {user_input}",
            skill_bundle_text="## Skills\n- cuda2ascend-simt",
            agent_factory=factory,
        ),
        Node(name="done", func=lambda state: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    state = runner.invoke("design trivial add op", thread_id="t1")

    # 验证状态推进
    assert state["current_phase"] == "done"
    assert state["phase_history"] == ["entry", "echo_design", "done"]

    # messages 累加
    assert len(state["messages"]) >= 2  # user + assistant
    assert state["messages"][0]["role"] == "user"
    assert state["messages"][0]["content"] == "design trivial add op"
    # 第二条起应有 assistant 响应
    assert any(m["role"] == "assistant" for m in state["messages"])

    # checkpoint 持久化
    assert store.get_status("t1") == STATUS_DONE
    reloaded = store.load("t1")
    assert reloaded["current_phase"] == "done"
    assert len(reloaded["messages"]) >= 2


def test_llm_node_rehydrates_conversation_history(tmp_path) -> None:
    """P0-2 关键:第二节点入口 agent._conversation_history == state["messages"]。"""
    store = CheckpointStore(tmp_path / "ck.db")
    seen_histories: list[list[dict]] = []

    def factory() -> FakeAgent:
        a = FakeAgent()
        # 拦截 run_conversation,记录入口 history
        orig_run = a.run_conversation

        def _spy_run(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
        ):
            seen_histories.append(list(a._conversation_history))
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        a.run_conversation = _spy_run  # type: ignore[method-assign]
        return a

    nodes = [
        make_llm_node(
            phase="first",
            task_prompt_template="phase1 {user_input}",
            agent_factory=factory,
        ),
        make_llm_node(
            phase="second",
            task_prompt_template="phase2 {user_input}",
            agent_factory=factory,
        ),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    state = runner.invoke("hello", thread_id="t1")

    # 第二节点入口 history 应包含第一节点产出的 user + assistant
    assert len(seen_histories) == 2
    first_entry_history = seen_histories[0]
    second_entry_history = seen_histories[1]

    # 第一节点入口 history == invoke() 写入的初始 user message
    assert first_entry_history == [{"role": "user", "content": "hello"}]

    # 第二节点入口 history 是第一节点结束后的 state["messages"]
    # = user(原始输入) + assistant(phase1 response),即 2 条
    # (make_llm_node 只把 assistant response append 到 messages,task prompt 不入)
    assert len(second_entry_history) == 2
    assert second_entry_history[0] == {"role": "user", "content": "hello"}
    assert second_entry_history[1]["role"] == "assistant"
    assert "phase1" in second_entry_history[1]["content"]

    # 与 state["messages"] 同形(零转换校验)
    # 最后历史 == state messages(因为第二节点也跑完了)
    assert state["messages"][-1]["role"] == "assistant"


def test_skill_bundle_text_injected_to_prompt_builder(tmp_path) -> None:
    """P0-1 关键:skills_layer_override 真的传到了 run_conversation。"""
    store = CheckpointStore(tmp_path / "ck.db")
    captured: dict[str, Any] = {}

    def factory() -> FakeAgent:
        a = FakeAgent()
        orig_run = a.run_conversation

        def _capture(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
        ):
            captured["override"] = skills_layer_override
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        a.run_conversation = _capture  # type: ignore[method-assign]
        return a

    skill_text = "## Available Skills (cuda2ascend-simt)\nreferences..."
    nodes = [
        make_llm_node(
            phase="design",
            task_prompt_template="design {user_input}",
            skill_bundle_text=skill_text,
            agent_factory=factory,
        ),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    runner.invoke("design add", thread_id="t1")

    assert captured["override"] == skill_text


def test_memory_pools_persist_across_nodes(tmp_path) -> None:
    """memory_pools 在第二节点 rehydrate 后 Layer 5 非空(防跨节点丢失)。"""
    store = CheckpointStore(tmp_path / "ck.db")
    factory = _factory_provider

    # 第一节点手动塞 memory_pool(模拟上一节点结果)
    def first_node(state):
        return {
            "memory_pools": {"memory": ["ctx-from-first"]},
        }

    # 第二节点:factory 创建 agent,rehydrate 后再调 LLM
    second_seen: list[str] = []
    orig_factory = factory

    def spying_factory():
        a = orig_factory()
        orig_run = a.run_conversation

        def _spy_run(
            user_input,
            skills_layer_override=None,
            *,
            task_type=None,
        ):
            # 验证 memory 已被 rehydrate 到 agent
            items = a.memory.get("memory")
            second_seen.extend(items)
            return orig_run(
                user_input,
                skills_layer_override,
                task_type=task_type,
            )

        a.run_conversation = _spy_run  # type: ignore[method-assign]
        return a

    nodes = [
        Node(name="first", func=first_node),
        make_llm_node(
            phase="second",
            task_prompt_template="phase2 {user_input}",
            agent_factory=spying_factory,
        ),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    runner.invoke("hello", thread_id="t1")

    assert "ctx-from-first" in second_seen


# ---- HITL 中断 ----


def test_hitl_interrupt_marks_waiting_confirm(tmp_path) -> None:
    """节点返回 __interrupt__:status=waiting_confirm + pending 存盘。"""
    store = CheckpointStore(tmp_path / "ck.db")

    def design_with_hitl(state):
        return {
            "design_doc": {"name": "add"},
            "__interrupt__": {
                "phase": "design",
                "message": "approve design?",
                "options": ["yes", "no"],
            },
        }

    nodes = [
        Node(name="entry", func=lambda s: {}),
        Node(name="design", func=design_with_hitl),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    state = runner.invoke("design add", thread_id="t1")

    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    assert state["current_phase"] == "design"
    assert state["pending_confirmation"]["options"] == ["yes", "no"]
    # design_doc 已落盘(中断前的普通字段 update)
    assert state["design_doc"]["name"] == "add"

    pending = store.consume_pending("t1")
    assert pending is not None
    assert pending.phase == "design"
    # 注意:consume_pending 已把 status 重置为 running 并删除 pending
    assert store.get_status("t1") == STATUS_RUNNING


def test_resume_from_hitl_re_runs_same_node_with_pending(tmp_path) -> None:
    """HITL resume:重跑同一节点,state.pending_confirmation 已注入。"""
    store = CheckpointStore(tmp_path / "ck.db")

    call_count = {"n": 0}

    def design_with_idempotent_check(state):
        call_count["n"] += 1
        pending = state.get("pending_confirmation")
        if pending is not None:
            # 第二次调用:已有 confirmation,跳过 LLM,推进
            return {"pending_confirmation": None, "last_phase_result": {"approved": True}}

        # 第一次调用:产出 design 并请求确认
        return {
            "design_doc": {"name": "add"},
            "__interrupt__": {"phase": "design", "message": "approve?"},
        }

    nodes = [
        Node(name="entry", func=lambda s: {}),
        Node(name="design", func=design_with_idempotent_check),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)

    # 第一次:invoke 触发中断
    state = runner.invoke("design add", thread_id="t1")
    assert store.get_status("t1") == STATUS_WAITING_CONFIRM
    assert call_count["n"] == 1

    # 第二次:resume with payload
    state = runner.resume("t1", payload={"approved": True})
    assert call_count["n"] == 2  # 同一节点重跑
    assert state["pending_confirmation"] is None  # 节点主动清除
    assert state["last_phase_result"]["approved"] is True
    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE


# ---- 崩溃恢复 ----


def test_crash_resume_from_last_completed_phase(tmp_path) -> None:
    """崩溃恢复:status=running 但无 pending → 从下一节点续跑。"""
    store = CheckpointStore(tmp_path / "ck.db")
    events: list[str] = []

    def make_recording_node(name, response, fail=False):
        def _node(state):
            events.append(f"call:{name}")
            if fail:
                raise RuntimeError(f"crash in {name}")
            return {"messages": [{"role": "assistant", "content": response}]}

        return Node(name=name, func=_node)

    nodes = [
        make_recording_node("n1", "r1"),
        make_recording_node("n2", "r2", fail=True),  # 第一次崩溃
        make_recording_node("n3", "r3"),
        Node(name="done", func=lambda s: {"__status__": "done"}),
    ]

    # 第一跑:n1 完成,n2 崩溃
    runner1 = PhaseRunner(nodes=nodes, store=store)
    with pytest.raises(RuntimeError, match="crash in n2"):
        runner1.invoke("start", thread_id="t1")
    # n1 已完成落盘,n2 崩溃时存 status=failed + current_phase=n2
    assert "call:n1" in events
    assert "call:n2" in events
    assert "call:n3" not in events
    assert store.get_status("t1") == STATUS_FAILED

    # 第二跑:resume —— status=failed → 重跑 n2(成功),然后 n3, done
    events.clear()
    # 重置 n2 不再失败
    nodes[1] = make_recording_node("n2", "r2", fail=False)
    runner2 = PhaseRunner(nodes=nodes, store=store)
    state = runner2.resume("t1")

    # n2 这次成功,n3 也跑
    assert "call:n2" in events
    assert "call:n3" in events
    assert state["current_phase"] == "done"
    assert store.get_status("t1") == STATUS_DONE


def test_phase_callback_emits_started_completed(tmp_path) -> None:
    """phase_callback:每节点入口 started,出口 completed。"""
    store = CheckpointStore(tmp_path / "ck.db")
    events: list[tuple[str, str]] = []

    runner = PhaseRunner(
        nodes=[
            entry_node_factory(),
            make_llm_node(
                phase="design",
                task_prompt_template="design {user_input}",
                agent_factory=_factory_provider,
            ),
            Node(name="done", func=lambda s: {"__status__": "done"}),
        ],
        store=store,
        phase_callback=lambda phase, event, error=None: events.append((phase, event)),
    )
    runner.invoke("design add", thread_id="t1")

    assert ("entry", "started") in events
    assert ("entry", "completed") in events
    assert ("design", "started") in events
    assert ("design", "completed") in events
    assert ("done", "started") in events
    assert ("done", "completed") in events


def test_failed_status_terminal(tmp_path) -> None:
    """节点返回 __status__='failed':立即终止,status=failed。"""
    store = CheckpointStore(tmp_path / "ck.db")
    later_called = {"n": False}

    def fail_node(state):
        return {"__status__": STATUS_FAILED}

    def later_node(state):
        later_called["n"] = True
        return {}

    nodes = [
        Node(name="entry", func=lambda s: {}),
        Node(name="bad", func=fail_node),
        Node(name="later", func=later_node),
    ]
    runner = PhaseRunner(nodes=nodes, store=store)
    state = runner.invoke("start", thread_id="t1")

    assert state["current_phase"] == "bad"
    assert store.get_status("t1") == STATUS_FAILED
    assert later_called["n"] is False  # later 未跑
