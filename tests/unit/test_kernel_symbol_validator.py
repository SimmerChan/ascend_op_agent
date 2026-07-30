"""U2.5:kernel 入口符号自校验单测。

覆盖 validate_kernel_symbol + _kernel_symbol_validator 节点行为:
- happy PascalCase: kernel `OpAdd` + op_api `l0op::OpAdd` 通过
- snake_case alternative: kernel `op_add` + op_api `l0op::OpAdd` 通过(两边交集)
- mismatch: kernel `SomeKernel` + op_api `l0op::OpAdd` fail
- missing kernel file: fail
- missing op_api file: fail
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_files(kernel_entry: str | None, op_api_l0op: str | None) -> list[dict]:
    """构造 mock code_result.files(loader 注入 op_api + LLM 写 kernel)。"""
    kernel_path = "/tmp/op/op_kernel/op_add_arch22.cpp"
    op_api_path = "/tmp/op/op_api/aclnn_op_add.cpp"
    files = []
    if kernel_entry is not None:
        files.append(
            {
                "path": kernel_path,
                "content": (
                    f"__global__ __aicore__ void {kernel_entry}("
                    f"GM_ADDR x, GM_ADDR y, GM_ADDR z, GM_ADDR workspace, GM_ADDR tiling)"
                    " { /* kernel body */ }"
                ),
            }
        )
    if op_api_l0op is not None:
        files.append(
            {
                "path": op_api_path,
                "content": (
                    f"auto result = l0op::{op_api_l0op}(x1Contiguous, x2Contiguous, executor.get());"
                ),
            }
        )
    return files


# ---- validate_kernel_symbol 函数单测 ----


def test_validate_kernel_symbol_snake_case_match():
    """happy:kernel 入口 snake_case `op_add` 通过(CANN 9.1.0 文件名 stem 规则)。"""
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    files = _make_files(kernel_entry="op_add", op_api_l0op="OpAdd")
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is None, f"snake_case 入口应通过,实际 {result}"


def test_validate_kernel_symbol_pascalcase_rejected():
    """CANN 9.1.0 拒 PascalCase kernel 入口(infer compile info 阶段):
    `kernel entry 'op_add' not implement in 'op_add_arch22.cpp'`。validator 应 fast-fail。"""
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    files = _make_files(kernel_entry="OpAdd", op_api_l0op="OpAdd")
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "must contain snake_case 'op_add'" in result["error"]
    assert "CANN 9.1.0 filename stem rule" in result["error"]


def test_validate_kernel_symbol_pascalcase_kernel_mismatch():
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    files = _make_files(kernel_entry="SomeOtherKernel", op_api_l0op="OpAdd")
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "must contain snake_case 'op_add'" in result["error"]
    assert "SomeOtherKernel" in result["kernel_symbols"]


def test_validate_kernel_symbol_op_api_missing_l0op_call():
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    # kernel 入口 = op_add(对),op_api 调用 = 无 l0op:: -> fail
    files = _make_files(kernel_entry="op_add", op_api_l0op=None)
    # 用 _make_files 的 op_api_l0op=None 时已跳过 op_api file 写入,模拟 missing
    # 这 case 实际已被 missing_op_api_file 覆盖,改测 op_api 文件存在但无 l0op:: 调用
    files = [
        {
            "path": "/tmp/op/op_kernel/op_add_arch22.cpp",
            "content": "__global__ __aicore__ void op_add(GM_ADDR x, ...) {}",
        },
        {
            "path": "/tmp/op/op_api/aclnn_op_add.cpp",
            "content": "// 无 l0op:: 调用,只是注释",
        },
    ]
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "no `l0op::NAME(` call" in result["error"]


def test_validate_kernel_symbol_missing_kernel_file():
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    # 仅 op_api,无 kernel
    files = _make_files(kernel_entry=None, op_api_l0op="OpAdd")
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "missing kernel file" in result["error"]


def test_validate_kernel_symbol_missing_op_api_file():
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    # 仅 kernel,无 op_api
    files = _make_files(kernel_entry="OpAdd", op_api_l0op=None)
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "missing op_api file" in result["error"]


def test_validate_kernel_symbol_no_kernel_entry():
    """kernel cpp 不含 __global__ __aicore__ void NAME( -> 校验失败。"""
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    files = [
        {
            "path": "/tmp/op/op_kernel/op_add_arch22.cpp",
            "content": "// 没有 kernel 入口,只是注释",
        },
        {
            "path": "/tmp/op/op_api/aclnn_op_add.cpp",
            "content": "auto r = l0op::OpAdd(x, y);",
        },
    ]
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "no `__global__ __aicore__ void NAME(`" in result["error"]


def test_validate_kernel_symbol_no_op_api_call():
    """op_api cpp 不含 l0op::NAME( -> 校验失败(kernel snake_case 已合规)。"""
    from ascend_op_agent.orchestrator.nodes.common import validate_kernel_symbol

    files = [
        {
            "path": "/tmp/op/op_kernel/op_add_arch22.cpp",
            "content": "__global__ __aicore__ void op_add(GM_ADDR x, ...) {}",
        },
        {
            "path": "/tmp/op/op_api/aclnn_op_add.cpp",
            "content": "// 没有 l0op:: 调用,只是注释",
        },
    ]
    result = validate_kernel_symbol(files, "op_add", "OpAdd")
    assert result is not None
    assert "no `l0op::NAME(` call" in result["error"]


# ---- _kernel_symbol_validator 节点单测 ----


def test_kernel_symbol_validator_node_passes():
    """happy:state 含匹配符号 + op_info,validator 返回 passed=True。"""
    from ascend_op_agent.orchestrator.graphs.new_dev import build_new_dev_graph
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = CheckpointStore.from_config(_Cfg(db_path=str(Path(tmp) / "ckpt.db")))
        graph = build_new_dev_graph(
            store=store,
            agent_factory=lambda: None,
            phase_callback=lambda *a, **kw: None,
        )
        validator = next(
            n for n in graph.nodes if getattr(n, "name", "") == "kernel_symbol_validator"
        )
        # CANN 9.1.0:kernel 入口 snake_case `op_add`,op_api l0op::OpAdd 独立
        files = _make_files(kernel_entry="op_add", op_api_l0op="OpAdd")
        state = {
            "op_info": {"name": "op_add", "class_name": "OpAdd"},
            "code_result": {"files": files},
        }
        update = validator.func(state)
        assert update["last_phase_result"]["passed"] is True


def test_kernel_symbol_validator_node_raises_on_pascalcase():
    """CANN 拒:kernel `OpAdd` PascalCase -> raise ValueError(9.1.0 filename stem rule)。"""
    import pytest
    from ascend_op_agent.orchestrator.graphs.new_dev import build_new_dev_graph
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = CheckpointStore.from_config(_Cfg(db_path=str(Path(tmp) / "ckpt.db")))
        graph = build_new_dev_graph(
            store=store,
            agent_factory=lambda: None,
            phase_callback=lambda *a, **kw: None,
        )
        validator = next(
            n for n in graph.nodes if getattr(n, "name", "") == "kernel_symbol_validator"
        )
        files = _make_files(kernel_entry="OpAdd", op_api_l0op="OpAdd")
        state = {
            "op_info": {"name": "op_add", "class_name": "OpAdd"},
            "code_result": {"files": files},
        }
        with pytest.raises(ValueError) as exc_info:
            validator.func(state)
        assert "kernel_symbol_validator failed" in str(exc_info.value)
        assert "must contain snake_case 'op_add'" in str(exc_info.value)
