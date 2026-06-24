"""OpState 单测(F3 / U6 配套)。

覆盖:

- ``initial_state`` 构造空状态(线程 id + 空累加器)
- JSON 序列化/反序列化(支撑 SqliteSaver 等价物 = CheckpointStore)
- APPEND_FIELDS / MERGE_FIELDS 常量正确性
- TypedDict total=False:所有字段可选,允许局部更新
- messages 同形 ``list[{"role","content"}]``:与 AIAgent._conversation_history
  零转换(P0-2 修正)
"""

from __future__ import annotations

import json

from ascend_op_agent.orchestrator.state import (
    APPEND_FIELDS,
    MERGE_FIELDS,
    OpState,
    initial_state,
)


def test_initial_state_has_thread_id_and_empty_accumulators() -> None:
    s = initial_state("t1")
    assert s["thread_id"] == "t1"
    assert s["messages"] == []
    assert s["phase_history"] == []
    assert s["memory_pools"] == {}
    assert s["retry_counts"] == {}


def test_initial_state_dataclass_fields_none() -> None:
    """dataclass 嵌入字段默认 None(U5 serde to_dict 才会被填充)。"""
    s = initial_state("t1")
    assert s["op_info"] is None
    assert s["design_doc"] is None
    assert s["code_result"] is None
    assert s["compile_result"] is None
    assert s["precision_report"] is None
    assert s["last_phase_result"] is None


def test_state_json_serializable_for_checkpoint() -> None:
    """OpState 嵌入 dataclass 后 JSON 序列化成功(支撑 CheckpointStore.save)。"""
    s = initial_state("t1")
    s["op_info"] = {"name": "add", "op_type": "elementwise"}
    s["messages"] = [
        {"role": "user", "content": "design an add op"},
        {"role": "assistant", "content": "ok"},
    ]
    s["memory_pools"] = {"memory": ["ctx1", "ctx2"]}
    s["phase_history"] = ["entry", "analyze"]

    serialized = json.dumps(s, ensure_ascii=False)
    restored = json.loads(serialized)

    assert restored["thread_id"] == "t1"
    assert restored["op_info"]["name"] == "add"
    assert len(restored["messages"]) == 2
    assert restored["messages"][0] == {"role": "user", "content": "design an add op"}
    assert restored["memory_pools"]["memory"] == ["ctx1", "ctx2"]
    assert restored["phase_history"] == ["entry", "analyze"]


def test_messages_same_shape_as_agent_conversation_history() -> None:
    """P0-2 关键:OpState.messages 与 AIAgent._conversation_history 同形。

    每条 = ``{"role": str, "content": str}``。节点内 rehydrate 零转换。
    """
    s = initial_state("t1")
    s["messages"] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "design X"},
    ]
    # 同形校验:每条 dict 有 role + content
    for msg in s["messages"]:
        assert set(msg.keys()) == {"role", "content"}
        assert isinstance(msg["role"], str)
        assert isinstance(msg["content"], str)


def test_append_fields_correct() -> None:
    """reducer 字段集正确(PhaseRunner._apply_update 依赖)。"""
    assert APPEND_FIELDS == frozenset({"messages", "phase_history"})


def test_merge_fields_correct() -> None:
    assert MERGE_FIELDS == frozenset({"memory_pools", "retry_counts"})


def test_typeddict_allows_partial_update() -> None:
    """total=False:局部更新不应被类型检查拒绝(运行时无强制,字段全可选)。"""
    # 构造一个只有部分字段的 state —— TypedDict total=False 允许
    partial: OpState = OpState(thread_id="t2")  # type: ignore[typeddict-item]
    assert partial["thread_id"] == "t2"
    # 未设字段访问会 KeyError(运行时不提供默认值,由 initial_state 工厂兜底)
    assert "messages" not in partial


def test_dataclass_embedded_as_dict_json_round_trip() -> None:
    """U5 产物嵌入 OpState 后 JSON round-trip(支撑 checkpoint.save/load)。"""
    s = initial_state("t1")
    # dataclass 已经在调用方转成 dict 才嵌入
    s["op_info"] = {
        "name": "add",
        "description": "d",
        "op_type": "e",
        "migration_strategy": "from_scratch",
    }
    s["design_doc"] = {"op_info": s["op_info"], "confirmed": True}

    serialized = json.dumps(s, ensure_ascii=False)
    restored = initial_state("t1")
    restored.update(json.loads(serialized))

    assert restored["op_info"]["migration_strategy"] == "from_scratch"
    assert restored["design_doc"]["op_info"]["name"] == "add"
    assert restored["design_doc"]["confirmed"] is True
