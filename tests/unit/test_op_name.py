"""U3:op 名解析 + invoke op_info 透传单测(方向 B)。

覆盖:
- to_pascal: snake_case -> PascalCase
- PhaseRunner.invoke op_info 参数透传到 state.op_info
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.nodes.common import to_pascal


# ---- to_pascal ----


def test_to_pascal_basic():
    assert to_pascal("vector_add") == "VectorAdd"
    assert to_pascal("add") == "Add"
    assert to_pascal("dot_product") == "DotProduct"
    assert to_pascal("softmax") == "Softmax"


def test_to_pascal_single_word():
    assert to_pascal("relu") == "Relu"


def test_to_pascal_leading_trailing_underscore():
    assert to_pascal("_vector_add") == "VectorAdd"
    assert to_pascal("vector_add_") == "VectorAdd"
    assert to_pascal("vector__add") == "VectorAdd"


def test_to_pascal_empty():
    assert to_pascal("") == ""
    assert to_pascal("_") == ""


# ---- invoke op_info 透传 ----


def test_invoke_op_info_propagated_to_state():
    """invoke(op_info=...) 应设 state["op_info"](codegen 从取 op 名参数化 scaffold)。"""
    from ascend_op_agent.orchestrator.state_machine import PhaseRunner

    # mock store + initial_state(不真跑节点)
    saved = {}

    class _FakeStore:
        def save(self, thread_id, state, current_phase="", status=""):
            saved["state"] = dict(state)
            saved["thread_id"] = thread_id

        def load(self, thread_id):
            return saved.get("state", {})

    def _initial_state(thread_id):
        return {"thread_id": thread_id, "messages": []}

    # 构造 PhaseRunner 不跑 _run_from(mock nodes)
    runner = PhaseRunner.__new__(PhaseRunner)
    runner.store = _FakeStore()
    runner.initial_state = _initial_state
    runner.nodes = []  # 空节点,_run_from 立即返回
    runner.phase_order = []

    # mock _run_from 直接返 state(不真跑节点)
    runner._run_from = lambda state, start_index=0: state

    runner.invoke(
        "op_desc",
        thread_id="t1",
        op_info={"name": "vector_add", "class_name": "VectorAdd"},
    )
    assert saved["state"].get("op_info") == {"name": "vector_add", "class_name": "VectorAdd"}


def test_invoke_without_op_info_backward_compatible():
    """不传 op_info(默认 None)-> state 不含 op_info(向后兼容)。"""
    from ascend_op_agent.orchestrator.state_machine import PhaseRunner

    saved = {}

    class _FakeStore:
        def save(self, thread_id, state, current_phase="", status=""):
            saved["state"] = dict(state)

        def load(self, thread_id):
            return saved.get("state", {})

    runner = PhaseRunner.__new__(PhaseRunner)
    runner.store = _FakeStore()
    runner.initial_state = lambda tid: {"thread_id": tid, "messages": []}
    runner.nodes = []
    runner.phase_order = []
    runner._run_from = lambda state, start_index=0: state

    runner.invoke("op_desc", thread_id="t1")
    assert "op_info" not in saved["state"] or saved["state"].get("op_info") is None
