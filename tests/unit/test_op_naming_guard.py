"""U5:LLM 命名飘移防御单测。

覆盖:
- parse_op_info_block 抽 <<OP_INFO>>{json}<<END>> 结构化块
  (含 fallback class_name / 缺块 / JSON 解析错 / 类型错)
- enforce_op_naming rename 语义目录里 LLM 幻觉的 add_custom_* / AddExample_*
  (2026-07-29 e2e 实证残留)
- make_llm_node 注入:response 末尾 OP_INFO 块 → state.op_info
- make_llm_node 注入:code_result.files 错前缀 → 自动 rename + 报告次数
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# 让 common.py 能 from 进来
_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---- parse_op_info_block ----


def test_parse_op_info_block_basic():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    r = '解析:op_add 算子...\n<<OP_INFO>>{"name": "op_add", "class_name": "OpAdd"}<<END>>'
    assert parse_op_info_block(r) == {"name": "op_add", "class_name": "OpAdd"}


def test_parse_op_info_block_missing_class_name_fallback():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    r = '<<OP_INFO>>{"name": "vector_add"}<<END>>'
    # class_name 缺 → to_pascal("vector_add") = "VectorAdd"
    assert parse_op_info_block(r) == {
        "name": "vector_add",
        "class_name": "VectorAdd",
    }


def test_parse_op_info_block_no_block_returns_none():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    assert parse_op_info_block("plain text no block") is None


def test_parse_op_info_block_invalid_json_returns_none():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    r = "<<OP_INFO>>{not valid json}<<END>>"
    assert parse_op_info_block(r) is None


def test_parse_op_info_block_missing_name_returns_none():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    r = '<<OP_INFO>>{"class_name": "OpAdd"}<<END>>'
    assert parse_op_info_block(r) is None


def test_parse_op_info_block_name_not_string_returns_none():
    from ascend_op_agent.orchestrator.nodes.common import parse_op_info_block

    r = '<<OP_INFO>>{"name": 123}<<END>>'
    assert parse_op_info_block(r) is None


# ---- enforce_op_naming ----


def test_enforce_op_naming_renames_add_custom_in_op_host():
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        {"path": "/tmp/op/op_host/add_custom_def.cpp", "content": "AddCustom"},
        {"path": "/tmp/op/op_host/add_custom_infershape.cpp", "content": "AddCustom"},
        {"path": "/tmp/op/op_host/op_add_def.cpp", "content": "OpAdd"},  # 已正确,不动
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 2
    paths = {f["path"] for f in out}
    assert "/tmp/op/op_host/op_add_def.cpp" in paths
    assert "/tmp/op/op_host/op_add_infershape.cpp" in paths
    # content 同步替换
    renamed = next(
        f for f in out if f["path"] == "/tmp/op/op_host/op_add_def.cpp" and f["content"] == "OpAdd"
    )
    assert renamed["content"] == "OpAdd"


def test_enforce_op_naming_renames_add_example_in_op_kernel():
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        {"path": "/tmp/op/op_kernel/add_example_arch22.cpp", "content": ""},
        {"path": "/tmp/op/op_kernel/arch22/add_example.h", "content": ""},
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 2
    paths = {f["path"] for f in out}
    assert "/tmp/op/op_kernel/op_add_arch22.cpp" in paths
    assert "/tmp/op/op_kernel/arch22/op_add.h" in paths


def test_enforce_op_naming_leaves_op_graph_correctly_named():
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        {"path": "/tmp/op/op_graph/op_add_proto.h", "content": ""},
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 0
    assert out[0]["path"] == "/tmp/op/op_graph/op_add_proto.h"


def test_enforce_op_naming_skips_non_semantic_dirs():
    """非 op_host/op_kernel/op_graph 目录不动(如 tests/st scaffold 模板)。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        # tests/st/run.sh 包名不算"语义目录",不动
        {"path": "/tmp/op/tests/st/test_aclnn_op_add.cpp", "content": ""},
        {"path": "/tmp/op/build.sh", "content": ""},
        # examples 目录也不动
        {"path": "/tmp/op/examples/aclnn_example.cpp", "content": ""},
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 0
    assert out == files


def test_enforce_op_naming_empty_op_snake_is_noop():
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [{"path": "/tmp/op/op_host/add_custom_def.cpp", "content": ""}]
    out, n = enforce_op_naming(files, "")
    assert n == 0
    assert out == files  # 没 op_snake 时不动


# ---- make_llm_node 集成 ----


def _make_mock_agent(response: str, files_in_response: list[str] | None = None):
    """构造 mock AIAgent:response 含 OP_INFO 块 + 可选 markdown 代码块。

    files_in_response: 模拟 LLM 响应里的 markdown 代码块路径列表(每个路径
    生成最小内容,模拟 markdown fallback 提取)。

    实现关键:make_llm_node line 96 用 ``agent._conversation_history = list(state["messages"])``
    覆盖式 rehydrate,因此 markdown 代码块必须放 state["messages"](而不是 agent
    上预置)。mock agent 用 MagicMock 但暴露可写的 _conversation_history
    属性;rehydrate 后 state["messages"] 里的 assistant 消息会被 markdown fallback
    扫到。
    """
    agent = MagicMock()
    agent._tool_calls_log = []
    agent.memory.get.return_value = []
    agent.run_conversation.return_value = response
    return agent


def _state_with_files(user_input: str, files_in_response: list[str]) -> dict:
    """构造 state,messages 含模拟 LLM markdown 代码块响应。"""
    return {
        "messages": [
            {"role": "user", "content": user_input},
            {
                "role": "assistant",
                "content": "\n\n".join(
                    f"```cpp\n// {p}\nint x = 1;\n```" for p in files_in_response
                ),
            },
        ],
    }


def test_make_llm_node_extracts_op_info_from_response():
    """LLM response 含 <<OP_INFO>> 块 → state.op_info 写入。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    response = "算子分析完成...\n<<OP_INFO>>" '{"name": "op_add", "class_name": "OpAdd"}<<END>>'
    agent = _make_mock_agent(response)
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="分析 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    update = node.func({"messages": [{"role": "user", "content": "add"}]})
    assert update["op_info"] == {"name": "op_add", "class_name": "OpAdd"}
    # last_phase_result 也带 naming_renamed(0,无文件)
    assert update["last_phase_result"]["naming_renamed"] == 0


def test_make_llm_node_no_op_info_block_no_state_update():
    """response 无 OP_INFO 块 → update 不含 op_info 键(下游用 fallback)。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    response = "算子分析完成,无结构化块"
    agent = _make_mock_agent(response)
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="分析 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    update = node.func({"messages": [{"role": "user", "content": "add"}]})
    assert "op_info" not in update


def test_make_llm_node_renames_llm_hallucinated_filenames():
    """codegen 阶段 LLM 输出 add_custom_* 路径 + state.op_info.name="op_add"
    → enforce_op_naming 自动 rename + 报告 naming_renamed=2。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    response = "已生成算子代码"
    agent = _make_mock_agent(response)
    node = make_llm_node(
        phase="codegen_host",
        task_prompt_template="生成 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    state = _state_with_files(
        "host code",
        files_in_response=[
            "/tmp/op/op_host/add_custom_def.cpp",
            "/tmp/op/op_kernel/add_custom_arch22.cpp",
            "/tmp/op/op_host/op_add_infershape.cpp",  # 已正确,不动
        ],
    )
    state["op_info"] = {"name": "op_add", "class_name": "OpAdd"}
    update = node.func(state)
    assert "code_result" in update
    paths = {f["path"] for f in update["code_result"]["files"]}
    # 错前缀被 rename
    assert "/tmp/op/op_host/op_add_def.cpp" in paths
    assert "/tmp/op/op_kernel/op_add_arch22.cpp" in paths
    # 正确的不动
    assert "/tmp/op/op_host/op_add_infershape.cpp" in paths
    # 错前缀不残留
    assert not any("add_custom" in p for p in paths)
    # 报告 rename 次数
    assert update["code_result"]["naming_renamed"] == 2
    assert update["last_phase_result"]["naming_renamed"] == 2


def test_make_llm_node_no_state_op_info_no_rename():
    """state.op_info 缺(analyze 跑挂/单元测试)→ enforce_op_naming 跳过,
    不抛、不破坏原 files(降级策略:让下游 fallback op_add 处理)。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    response = "已生成"
    agent = _make_mock_agent(response)
    node = make_llm_node(
        phase="codegen_host",
        task_prompt_template="生成 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    state = _state_with_files(
        "host",
        files_in_response=["/tmp/op/op_host/add_custom_def.cpp"],
    )
    # 无 op_info → 降级不动
    update = node.func(state)
    assert "code_result" in update
    assert update["code_result"]["files"][0]["path"] == "/tmp/op/op_host/add_custom_def.cpp"
    assert update["code_result"]["naming_renamed"] == 0
