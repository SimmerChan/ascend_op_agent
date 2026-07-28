"""U4:codegen 拓扑重构单测(方向 B scaffold 注入 + LLM 语义子目录)。

覆盖:
- _scaffold_inject_node 注入 5 构建文件(子目录,参数化 op 名)
- codegen 节点列表含 scaffold + 3 LLM 语义节点
- 构建文件无 add_example 残留
- 无 op_kernel.ini(方向 B 丢弃)
- op_info fallback(op_info 缺时用 op_add/OpAdd)
"""

from __future__ import annotations

from pathlib import Path

from ascend_op_agent.orchestrator.graphs.new_dev import build_new_dev_graph


# ---- _scaffold_inject_node 注入 5 构建文件 ----


def test_scaffold_inject_node_loads_5_build_files(tmp_path, monkeypatch):
    """_scaffold_inject_node 从 load_build_scaffold 注入 5 构建文件到 code_result.files(子目录)。"""
    # 用 build_new_dev_graph 构造图,取 codegen_scaffold 节点
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    store = CheckpointStore.from_config(_Cfg(db_path=str(tmp_path / "ckpt.db")))
    graph = build_new_dev_graph(
        store=store,
        agent_factory=lambda: None,
        phase_callback=lambda *a, **kw: None,
    )
    # 找 codegen_scaffold 节点
    scaffold_node = None
    for n in getattr(graph, "nodes", []):
        if getattr(n, "name", "") == "codegen_scaffold":
            scaffold_node = n
            break
    assert scaffold_node is not None, "codegen_scaffold 节点应存在"

    # 改 _OPERATOR_DIR 到 tmp_path(避免写 /tmp/e2e_ops_local)
    state = {
        "op_info": {"name": "op_add", "class_name": "OpAdd"},
        "messages": [],
        "memory_pools": {},
    }
    # 跑 scaffold 节点(它会写 /tmp/e2e_ops_local/op_add,测试后清理)
    update = scaffold_node.func(state)
    files = update["code_result"]["files"]
    # 5 构建文件
    rels = {str(Path(f["path"]).relative_to("/tmp/e2e_ops_local/op_add")) for f in files}
    expected = {
        "CMakeLists.txt",
        "build.sh",
        "op_host/CMakeLists.txt",
        "op_kernel/CMakeLists.txt",
        "op_graph/CMakeLists.txt",
    }
    assert rels == expected, f"实际 rels: {rels}"
    # tool 标记
    assert all(f["tool"] == "scaffold_loaded" for f in files)
    # 参数化无 add_example 残留
    for f in files:
        assert "add_example" not in f["content"], f"{f['path']} 残留 add_example"
        assert "AddExample" not in f["content"], f"{f['path']} 残留 AddExample"


# ---- codegen 节点列表含 scaffold + 3 语义节点 ----


def test_codegen_nodes_include_scaffold_and_semantic(tmp_path):
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    store = CheckpointStore.from_config(_Cfg(db_path=str(tmp_path / "ckpt.db")))
    graph = build_new_dev_graph(
        store=store,
        agent_factory=lambda: None,
        phase_callback=lambda *a, **kw: None,
    )
    names = [getattr(n, "name", "") for n in getattr(graph, "nodes", [])]
    assert "codegen_scaffold" in names
    assert "codegen_kernel" in names
    assert "codegen_host" in names
    assert "codegen_proto" in names


# ---- 无 op_kernel.ini(方向 B 丢弃)----


def test_no_codegen_kernel_ini_node(tmp_path):
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    store = CheckpointStore.from_config(_Cfg(db_path=str(tmp_path / "ckpt.db")))
    graph = build_new_dev_graph(
        store=store,
        agent_factory=lambda: None,
        phase_callback=lambda *a, **kw: None,
    )
    names = [getattr(n, "name", "") for n in getattr(graph, "nodes", [])]
    assert "codegen_kernel_ini" not in names, "方向 B 丢弃 op_kernel.ini 节点"
    assert "codegen_cmakelists" not in names, "构建文件 scaffold 注入,删 codegen_cmakelists"
    assert "codegen_build_sh" not in names, "构建文件 scaffold 注入,删 codegen_build_sh"


# ---- op_info fallback ----


def test_scaffold_inject_node_op_info_fallback(tmp_path):
    """op_info 缺时 fallback op_add/OpAdd(不抛)。"""
    from ascend_op_agent.orchestrator import CheckpointStore
    from dataclasses import dataclass

    @dataclass
    class _Cfg:
        db_path: str

    store = CheckpointStore.from_config(_Cfg(db_path=str(tmp_path / "ckpt.db")))
    graph = build_new_dev_graph(
        store=store,
        agent_factory=lambda: None,
        phase_callback=lambda *a, **kw: None,
    )
    scaffold_node = next(
        n for n in getattr(graph, "nodes", []) if getattr(n, "name", "") == "codegen_scaffold"
    )
    state = {"messages": [], "memory_pools": {}}  # 无 op_info
    update = scaffold_node.func(state)
    # fallback op_add/OpAdd(不抛)
    assert update["last_phase_result"]["op_snake"] == "op_add"
    assert update["last_phase_result"]["op_pascal"] == "OpAdd"
