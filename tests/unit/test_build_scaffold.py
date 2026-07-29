"""U2:load_build_scaffold 参数化 op 名单测(方向 B)。

从 vendor add_example 读 5 构建文件,参数化替换 add_example->{op_snake} /
AddExample->{op_pascal},返回 {relpath: content}(保留子目录)。
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.cannbot_loader import load_build_scaffold


# ---- 5 文件 + 参数化无残留 ----


def test_load_build_scaffold_returns_8_files_parameterized():
    out = load_build_scaffold("op_add", "OpAdd")
    # 5 构建文件 + 3 ST 驱动模板(tests/st/test_aclnn_op_add.cpp 文件名参数化)
    expected = {
        "CMakeLists.txt",
        "build.sh",
        "op_host/CMakeLists.txt",
        "op_kernel/CMakeLists.txt",
        "op_graph/CMakeLists.txt",
        "tests/st/test_aclnn_op_add.cpp",
        "tests/st/CMakeLists.txt",
        "tests/st/run.sh",
    }
    assert set(out.keys()) == expected, f"实际 keys: {set(out.keys())}"


def test_root_cmakelists_parameterized_no_residual():
    out = load_build_scaffold("op_add", "OpAdd")
    cmake = out["CMakeLists.txt"]
    # 正确范式
    assert "npu_op_package(" in cmake
    # 参数化替换
    assert "op_add_custom" in cmake  # package_name
    assert "op_add_op_prj" in cmake  # project
    # 无 add_example / AddExample 残留
    assert "add_example" not in cmake, f"根 CMakeLists 残留 add_example:\n{cmake}"
    assert "AddExample" not in cmake, f"根 CMakeLists 残留 AddExample:\n{cmake}"


def test_build_sh_parameterized():
    out = load_build_scaffold("op_add", "OpAdd")
    build_sh = out["build.sh"]
    # build.sh 通用(参数解析 + cmake 调用),参数化 op 名
    assert "-j*)" in build_sh  # 保留 -j* case
    assert "--soc=*)" in build_sh  # 保留 --soc case
    assert "add_example" not in build_sh, f"build.sh 残留 add_example"


def test_op_kernel_cmakelists_references_op_kernel_file():
    """op_kernel/CMakeLists.txt 的 KERNEL_FILE 应从 add_example_arch22.cpp -> op_add_arch22.cpp。"""
    out = load_build_scaffold("op_add", "OpAdd")
    cmake = out["op_kernel/CMakeLists.txt"]
    assert "op_add_arch22.cpp" in cmake, f"KERNEL_FILE 应参数化为 op_add_arch22.cpp:\n{cmake}"
    assert "add_example_arch22" not in cmake
    # OP_TYPE 参数化 AddExample -> OpAdd
    assert "OpAdd" in cmake
    assert "AddExample" not in cmake


def test_op_host_cmakelists_references_op_host_files():
    """op_host/CMakeLists.txt 引用 def/infershape/tiling 文件名,应参数化。"""
    out = load_build_scaffold("op_add", "OpAdd")
    cmake = out["op_host/CMakeLists.txt"]
    assert "op_add_def.cpp" in cmake
    assert "op_add_infershape.cpp" in cmake
    assert "add_example_def" not in cmake
    assert "AddExample" not in cmake


def test_op_host_cmakelists_strips_opapi_section():
    """方向 B 不注入 op_api/,op_host/CMakeLists.txt 移除 cust_opapi library 段 + package_add 引用。

    add_example 原版引用 op_api/aclnn_*.cpp 构建 cust_opapi -> CMake 'No SOURCES given to
    target cust_opapi'。strip 后 cust_opapi/op_api_dir 不存在,但 cust_optiling/cust_op_proto 保留。
    """
    out = load_build_scaffold("vector_add", "VectorAdd")
    cmake = out["op_host/CMakeLists.txt"]
    assert "cust_opapi" not in cmake, f"op_host/CMakeLists 应移除 cust_opapi:\n{cmake}"
    assert "op_api_dir" not in cmake, f"应移除 op_api_dir:\n{cmake}"
    # 其他 library 保留(compile 需要)
    assert "cust_optiling" in cmake
    assert "cust_op_proto" in cmake


def test_all_files_no_add_example_residual():
    """所有 5 文件都不应残留 add_example / AddExample(参数化彻底)。"""
    out = load_build_scaffold("vector_add", "VectorAdd")
    for rel, content in out.items():
        assert "add_example" not in content, f"{rel} 残留 add_example"
        assert "AddExample" not in content, f"{rel} 残留 AddExample"


# ---- 路径缺失降级 ----


def test_load_build_scaffold_missing_dir_returns_empty(monkeypatch):
    """add_example 目录不存在 -> 返空 dict,不抛(降级)。"""
    import pathlib

    from ascend_op_agent.orchestrator import cannbot_loader

    monkeypatch.setattr(cannbot_loader, "CANNBOT_ROOT", pathlib.Path("/nonexistent/path/xyz"))
    out = cannbot_loader.load_build_scaffold("op_add", "OpAdd")
    assert out == {}


# ---- 不同 op 名参数化 ----


def test_load_build_scaffold_different_op_names():
    out = load_build_scaffold("matmul", "Matmul")
    cmake = out["CMakeLists.txt"]
    assert "matmul_custom" in cmake
    assert "add_example" not in cmake
    # AddExample 类名在 op_kernel/op_host CMakeLists 的 OP_TYPE,不在根 CMakeLists
    kernel_cmake = out["op_kernel/CMakeLists.txt"]
    assert "Matmul" in kernel_cmake
    assert "AddExample" not in kernel_cmake


# ---- ST 驱动模板参数化 + build.sh/run.sh 包名修复 ----


def test_st_template_parameterized_no_residual():
    """tests/st/test_aclnn_op_add.cpp 参数化:aclnnAddExample->aclnnOpAdd /
    aclnn_add_example.h->aclnn_op_add.h / 文件名 test_aclnn_op_add / 无残留。"""
    out = load_build_scaffold("op_add", "OpAdd")
    st_cpp = out["tests/st/test_aclnn_op_add.cpp"]
    assert "aclnnOpAdd" in st_cpp, "API 名应参数化 aclnnAddExample->aclnnOpAdd"
    assert "aclnn_op_add.h" in st_cpp, "header 应参数化 aclnn_add_example.h->aclnn_op_add.h"
    assert "add_example" not in st_cpp, f"ST cpp 残留 add_example"
    assert "AddExample" not in st_cpp, f"ST cpp 残留 AddExample"
    # ComputeGolden 保留(LLM codegen_st 节点改算子语义;对 add 算子是 no-op)
    assert "ComputeGolden" in st_cpp


def test_st_cmakelists_references_op_add():
    """tests/st/CMakeLists.txt 引用 test_aclnn_op_add + op_add_custom(参数化)。"""
    out = load_build_scaffold("op_add", "OpAdd")
    cmake = out["tests/st/CMakeLists.txt"]
    assert "test_aclnn_op_add" in cmake
    assert "aclnn_op_add.h" in cmake
    assert "op_add_custom" in cmake
    assert "add_example" not in cmake


def test_build_sh_package_name_wildcard():
    """build.sh 包名检测改通配符(原硬编码 ubuntu -> find custom_opp_*.run)。"""
    out = load_build_scaffold("op_add", "OpAdd")
    build_sh = out["build.sh"]
    assert "custom_opp_ubuntu_aarch64.run" not in build_sh, "build.sh 残留 ubuntu 包名"
    assert 'find ${BUILD_PATH} -maxdepth 1 -name "custom_opp_*.run"' in build_sh


def test_run_sh_package_name_almalinux():
    """tests/st/run.sh install 段包名改 almalinux(910B 容器)。"""
    out = load_build_scaffold("op_add", "OpAdd")
    run_sh = out["tests/st/run.sh"]
    assert "./custom_opp_ubuntu_aarch64.run" not in run_sh, "run.sh 残留 ubuntu 包名"
    assert "./custom_opp_almalinux_aarch64.run" in run_sh
