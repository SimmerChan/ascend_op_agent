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


def test_enforce_op_naming_renames_arbitrary_semantic_prefix():
    """allowlist:LLM 发明任意语义名(``elementwise_add``,非 blocklist 已知)也 rename。
    2026-07-30 e2e 实证:LLM 把 op_add 漂移成 elementwise_add,旧 blocklist 挡不住。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        {
            "path": "/tmp/op/op_kernel/elementwise_add_arch22.cpp",
            "content": "__global__ __aicore__ void elementwise_add(GM_ADDR x) {}",
        },
        {
            "path": "/tmp/op/op_kernel/arch22/elementwise_add.h",
            "content": "class ElementwiseAdd {};",
        },
        {
            "path": "/tmp/op/op_host/elementwise_add_def.cpp",
            "content": "class ElementwiseAdd : public OpDef {};\nOP_ADD(ElementwiseAdd);",
        },
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 3
    paths = {f["path"] for f in out}
    assert "/tmp/op/op_kernel/op_add_arch22.cpp" in paths
    assert "/tmp/op/op_kernel/arch22/op_add.h" in paths
    assert "/tmp/op/op_host/op_add_def.cpp" in paths
    # 无 elementwise 残留(文件名)
    assert not any("elementwise" in p for p in paths)
    # content 同步:kernel 入口 + include + PascalCase 类名
    kernel = next(f for f in out if "op_add_arch22.cpp" in f["path"])
    assert "void op_add(GM_ADDR x)" in kernel["content"]
    assert "elementwise_add" not in kernel["content"]
    header = next(f for f in out if "arch22/op_add.h" in f["path"])
    assert "class OpAdd {}" in header["content"]
    assert "ElementwiseAdd" not in header["content"]


def test_enforce_op_naming_leaves_correct_op_snake_prefix():
    """已用 op_snake 前缀的语义文件不动(allowlist 正例)。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_naming

    files = [
        {"path": "/tmp/op/op_kernel/op_add_arch22.cpp", "content": "void op_add(){}"},
        {"path": "/tmp/op/op_host/op_add_def.cpp", "content": "class OpAdd{};"},
    ]
    out, n = enforce_op_naming(files, "op_add")
    assert n == 0
    assert out == files


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


# ---- enforce_op_class_naming(U6:def.cpp 类名确定性改写)----


_DEF_CPP_SNAKE_CLASS = """\
#include "register/op_def_registry.h"
namespace ops {
class op_add : public OpDef {
public:
    explicit op_add(const char* name) : OpDef(name)
    { this->Input("x1"); }
};
OP_ADD(op_add);
}
"""


_DEF_CPP_PASCAL_CLASS = """\
#include "register/op_def_registry.h"
namespace ops {
class OpAdd : public OpDef {
public:
    explicit OpAdd(const char* name) : OpDef(name)
    { this->Input("x1"); }
};
OP_ADD(OpAdd);
}
"""


def test_enforce_op_class_naming_rewrites_snake_to_pascal():
    """LLM 把 snake 名当类名写(``class op_add``+``OP_ADD(op_add)``)→ 改写到 OpAdd。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_class_naming

    files = [{"path": "/tmp/op/op_host/op_add_def.cpp", "content": _DEF_CPP_SNAKE_CLASS}]
    out, n = enforce_op_class_naming(files, "OpAdd")
    assert n == 1
    content = out[0]["content"]
    assert "class OpAdd : public OpDef" in content
    assert "explicit OpAdd(const char* name)" in content
    assert "OP_ADD(OpAdd)" in content
    # 原 snake 类名不残留
    assert "class op_add" not in content
    assert "OP_ADD(op_add)" not in content


def test_enforce_op_class_naming_leaves_correct_pascal():
    """类名已是 OpAdd → 不动(n=0)。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_class_naming

    files = [{"path": "/tmp/op/op_host/op_add_def.cpp", "content": _DEF_CPP_PASCAL_CLASS}]
    out, n = enforce_op_class_naming(files, "OpAdd")
    assert n == 0
    assert out[0]["content"] == _DEF_CPP_PASCAL_CLASS


def test_enforce_op_class_naming_skips_non_def_files():
    """非 def.cpp(无 ``public OpDef``)不动 —— kernel/host tiling 类名各异,不碰。"""
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_class_naming

    kernel = "class NsAddExampleOuter { __aicore__ void op_add(); };"
    files = [{"path": "/tmp/op/op_kernel/op_add_arch22.cpp", "content": kernel}]
    out, n = enforce_op_class_naming(files, "OpAdd")
    assert n == 0
    assert out[0]["content"] == kernel


def test_enforce_op_class_naming_empty_op_pascal_is_noop():
    from ascend_op_agent.orchestrator.nodes.common import enforce_op_class_naming

    files = [{"path": "/tmp/op/op_host/op_add_def.cpp", "content": _DEF_CPP_SNAKE_CLASS}]
    out, n = enforce_op_class_naming(files, "")
    assert n == 0
    assert out == files


def test_enforce_op_class_naming_after_enforce_op_naming_chains():
    """端到端链路:LLM 写 add_custom 全套 → enforce_op_naming 归一文件名到 op_add,
    再 enforce_op_class_naming 把类名提到 OpAdd(模拟 2026-07-30 e2e 失败现场)。"""
    from ascend_op_agent.orchestrator.nodes.common import (
        enforce_op_class_naming,
        enforce_op_naming,
    )

    add_custom_def = _DEF_CPP_SNAKE_CLASS.replace("op_add", "add_custom")
    files = [{"path": "/tmp/op/op_host/add_custom_def.cpp", "content": add_custom_def}]
    files, _ = enforce_op_naming(files, "op_add")
    # enforce_op_naming 把 add_custom 归一到 op_add(含类名 → class op_add)
    assert "class op_add : public OpDef" in files[0]["content"]
    files, n = enforce_op_class_naming(files, "OpAdd")
    assert n == 1
    assert "class OpAdd : public OpDef" in files[0]["content"]
    assert "OP_ADD(OpAdd)" in files[0]["content"]


# ---- make_llm_node:U6 pin op_info + class_patched 上报 ----


def test_make_llm_node_does_not_override_pinned_op_info():
    """调用方 invoke 时 pin 了 op_info → analyze LLM 产 add_custom 的 OP_INFO 块
    也不覆盖(state.op_info 对齐用户意图,杀 analyze→add_custom 飘移)。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    response = "分析完成\n<<OP_INFO>>" '{"name": "add_custom", "class_name": "AddCustom"}<<END>>'
    agent = _make_mock_agent(response)
    node = make_llm_node(
        phase="analyze",
        task_prompt_template="分析 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    state = {"messages": [{"role": "user", "content": "op_add"}]}
    state["op_info"] = {"name": "op_add", "class_name": "OpAdd"}  # 调用方 pin
    update = node.func(state)
    # pin 的 op_info 不被 LLM 的 add_custom 块覆盖
    assert "op_info" not in update


def test_make_llm_node_reports_class_patched_for_snake_def():
    """codegen_host LLM 产 snake 类名 def.cpp → code_result.class_patched=1。"""
    from ascend_op_agent.orchestrator.nodes.common import make_llm_node

    agent = _make_mock_agent("已生成")
    node = make_llm_node(
        phase="codegen_host",
        task_prompt_template="生成 {user_input}",
        agent_factory=lambda: agent,
        no_tools=True,
    )
    state = {
        "messages": [
            {"role": "user", "content": "host"},
            {
                "role": "assistant",
                "content": f"```cpp\n// /tmp/op/op_host/op_add_def.cpp\n{_DEF_CPP_SNAKE_CLASS}```",
            },
        ],
        "op_info": {"name": "op_add", "class_name": "OpAdd"},
    }
    update = node.func(state)
    assert update["code_result"]["class_patched"] == 1
    content = update["code_result"]["files"][0]["content"]
    assert "class OpAdd : public OpDef" in content
    assert "OP_ADD(OpAdd)" in content
