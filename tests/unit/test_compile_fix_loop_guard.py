"""U5:make_real_compile_fix_loop_node guard + 错误分流单测(强化 fix_loop A2+A3)。

覆盖:
- _classify_stderr 纯函数:构建 vs kernel
- 前置扫:CMakeLists 含 ascendc_add_ops / build.sh 缺 -j*) → 首轮 fatal 早停
- stderr fatal:Unknown CMake command / ascendc_add_ops / Unknown option: -j → 早停
- 分流:构建配置错 → 模板重置(不经 LLM),fix_node 不被调
- 分流:kernel 错 → fix_node(不误杀)
- 合法 kernel 错正常进 fix_node(回归)
"""

from __future__ import annotations

from ascend_op_agent.orchestrator.nodes.validation import (
    _check_stderr_fatal,
    _classify_stderr,
    _scan_build_files_violation,
    make_real_compile_fix_loop_node,
)


# ---- mocks ----


class _FakeMemory:
    def __init__(self) -> None:
        self._pools: dict[str, list[str]] = {}

    def add(self, pool: str, content: str) -> None:
        self._pools.setdefault(pool, []).append(content)

    def get(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))


class _FakeAgent:
    def __init__(self, response: str = "ok") -> None:
        self._conversation_history: list[dict[str, str]] = []
        self.memory = _FakeMemory()
        self._tool_calls_log: list[dict] = []
        self._response = response
        self.call_count = 0

    def run_conversation(
        self,
        user_input,
        skills_layer_override=None,
        *,
        task_type=None,
        no_tools=False,
    ) -> str:
        self.call_count += 1
        self._conversation_history.append({"role": "user", "content": user_input})
        self._conversation_history.append({"role": "assistant", "content": self._response})
        return self._response


class _FakeExecutor:
    def __init__(self, results: list[dict]) -> None:
        self._results = list(results)
        self.calls: list[str] = []

    def compile_to_dict(self, operator_path: str) -> dict:
        self.calls.append(operator_path)
        return self._results.pop(0)


def _base_state(code_result: dict | None = None) -> dict:
    return {"code_result": code_result, "messages": [], "memory_pools": {}}


def _resolver(path: str):
    return lambda _state: path


def _ok_compile_result(path: str = "/tmp/op") -> dict:
    return {
        "success": True,
        "return_code": 0,
        "stdout": "ok",
        "stderr": "",
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


def _fail_compile_result(stderr: str, path: str = "/tmp/op") -> dict:
    return {
        "success": False,
        "return_code": 1,
        "stdout": "",
        "stderr": stderr,
        "command": "build.sh",
        "operator_path": path,
        "soc_version": "ascend910b",
    }


def _compliant_cmakelists() -> str:
    return "cmake_minimum_required(VERSION 3.16)\nnpu_op_package(my_op TYPE RUN)\n"


def _compliant_buildsh() -> str:
    return '#!/bin/bash\ncase "$1" in\n  -j*) THREAD_NUM="${1:2}";;\n  --soc=*) SOC="${1#*=}";;\nesac\n'


def _compliant_state() -> dict:
    return {
        "code_result": {
            "files": [
                {"path": "/tmp/op/CMakeLists.txt", "content": _compliant_cmakelists()},
                {"path": "/tmp/op/build.sh", "content": _compliant_buildsh()},
            ]
        },
        "messages": [],
        "memory_pools": {},
    }


# ---- 纯函数:_classify_stderr ----


def test_classify_stderr_build_signatures():
    assert _classify_stderr("CMake Error: invalid specification") == "build"
    assert _classify_stderr("Unknown option: -j8") == "build"
    assert _classify_stderr("ASCEND_CANN_PACKAGE_PATH not set") == "build"
    assert _classify_stderr("SOC_VERSION undefined") == "build"
    assert _classify_stderr("ascendc.cmake not found") == "build"


def test_classify_stderr_kernel_errors():
    assert _classify_stderr("syntax error in kernel at line 5") == "kernel"
    assert _classify_stderr("undefined identifier: GetVar") == "kernel"
    assert _classify_stderr("") == "kernel"


# ---- 纯函数:_check_stderr_fatal ----


def test_check_stderr_fatal():
    assert (
        _check_stderr_fatal('error: Unknown CMake command "ascendc_add_ops"')
        == "stderr_unknown_cmake_command"
    )
    assert _check_stderr_fatal("error: ascendc_add_ops not found") == "stderr_ascendc_add_ops"
    assert _check_stderr_fatal("Unknown option: -j") == "stderr_unknown_option_j"
    assert _check_stderr_fatal("syntax error") is None
    assert _check_stderr_fatal("") is None


# ---- 纯函数:_scan_build_files_violation ----


def test_scan_build_files_violation_cmake_ascendc_add_ops():
    files = [{"path": "/tmp/op/CMakeLists.txt", "content": "ascendc_add_ops(x)"}]
    assert _scan_build_files_violation(files) == "cmake_uses_ascendc_add_ops"


def test_scan_build_files_violation_cmake_missing_npu_op_package():
    files = [{"path": "/tmp/op/CMakeLists.txt", "content": "cmake_minimum_required(3.16)\n"}]
    assert _scan_build_files_violation(files) == "cmake_missing_npu_op_package"


def test_scan_build_files_violation_buildsh_missing_j_case():
    files = [
        {"path": "/tmp/op/CMakeLists.txt", "content": _compliant_cmakelists()},
        {"path": "/tmp/op/build.sh", "content": "#!/bin/bash\necho no j case\n"},
    ]
    assert _scan_build_files_violation(files) == "build_sh_missing_j_case"


def test_scan_build_files_violation_compliant():
    files = [
        {"path": "/tmp/op/CMakeLists.txt", "content": _compliant_cmakelists()},
        {"path": "/tmp/op/build.sh", "content": _compliant_buildsh()},
    ]
    assert _scan_build_files_violation(files) is None


def test_scan_build_files_violation_subdir_cmake_not_mistaken_for_root():
    """files 含子目录 CMakeLists(op_host/op_kernel,不含 npu_op_package)在前 + 根 CMakeLists
    (含)在后,guard fallback 必须选根,不误判 cmake_missing_npu_op_package。

    实测 910B spike:spike operator_path 是远程路径,本地磁盘读失败走 fallback,
    files[0]=op_host/CMakeLists(用 npu_op_kernel_sources)被误选 -> 假阳拦 compile。
    """
    files = [
        {"path": "/tmp/op/op_host/CMakeLists.txt", "content": "npu_op_kernel_sources(x)\n"},
        {"path": "/tmp/op/op_kernel/CMakeLists.txt", "content": "npu_op_code_gen(x)\n"},
        {"path": "/tmp/op/op_graph/CMakeLists.txt", "content": "npu_op_kernel_sources(y)\n"},
        {"path": "/tmp/op/CMakeLists.txt", "content": _compliant_cmakelists()},
        {"path": "/tmp/op/build.sh", "content": _compliant_buildsh()},
    ]
    assert _scan_build_files_violation(files, operator_path="") is None


# ---- 前置扫 → 首轮 fatal 早停(不调 compile) ----


def test_pre_scan_ascendc_add_ops_fatal_no_compile(tmp_path):
    # U5 补强:前置扫扫磁盘(operator_path/CMakeLists.txt)优先于 code_result.files。
    # 写违禁 CMakeLists 到 tmp_path 磁盘,验磁盘扫描拦。
    (tmp_path / "CMakeLists.txt").write_text("ascendc_add_ops(x)", encoding="utf-8")
    state = {"code_result": {"files": []}, "messages": [], "memory_pools": {}}
    executor = _FakeExecutor([])  # 不应被调
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver(str(tmp_path)),
        agent_factory=lambda: _FakeAgent(),
        max_rounds=3,
    )
    update = node.func(state)
    assert len(executor.calls) == 0, f"前置扫应早停,实际 compile 调了 {len(executor.calls)} 次"
    assert update["compile_fix_loop_result"]["status"] == "failed"
    reason = update["compile_fix_loop_result"]["reason"]
    assert "guard_build_template_violation" in reason
    assert "cmake_uses_ascendc_add_ops" in reason


def test_pre_scan_build_sh_missing_j_fatal(tmp_path):
    # U5 补强:磁盘扫 build.sh 缺 -j*) case -> fatal。
    (tmp_path / "CMakeLists.txt").write_text(_compliant_cmakelists(), encoding="utf-8")
    (tmp_path / "build.sh").write_text("#!/bin/bash\necho no j\n", encoding="utf-8")
    state = {"code_result": {"files": []}, "messages": [], "memory_pools": {}}
    executor = _FakeExecutor([])
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver(str(tmp_path)),
        agent_factory=lambda: _FakeAgent(),
        max_rounds=3,
    )
    update = node.func(state)
    assert len(executor.calls) == 0
    reason = update["compile_fix_loop_result"]["reason"]
    assert "build_sh_missing_j_case" in reason


# ---- stderr fatal 早停(compile 跑了,fix_node 不被调) ----


def test_stderr_unknown_cmake_command_fatal_no_fix():
    agent = _FakeAgent()
    executor = _FakeExecutor([_fail_compile_result('Unknown CMake command "foo"')])
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: agent,
        max_rounds=3,
    )
    update = node.func(_compliant_state())
    assert executor.calls == ["/tmp/op"]
    assert agent.call_count == 0, "stderr fatal 应早停,fix_node 不被调"
    reason = update["compile_fix_loop_result"]["reason"]
    assert "guard_stderr_fatal" in reason
    assert "stderr_unknown_cmake_command" in reason


# ---- 分流:构建配置错 → 模板重置(fix_node 不被调) ----


def test_build_error_template_reset_skips_fix_node():
    """stderr 含构建签名 → 模板重置 add_example_raw 到 build.sh/CMakeLists.txt,fix_node 不被调。"""
    add_example_raw = {
        # 合规:含 npu_op_package( 通过前置扫 + RESET marker 标识重置来源
        "CMakeLists.txt": "npu_op_package(my_op RESET)\n",
        # 合规:含 -j*) case 通过前置扫 + RESET marker 标识
        "build.sh": _compliant_buildsh() + "\n# RESET_BUILD_SH_MARKER\n",
    }
    state = _compliant_state()
    agent = _FakeAgent()
    # stderr 含 CMake + SOC_VERSION(构建签名) → 走模板重置
    executor = _FakeExecutor(
        [
            _fail_compile_result("CMake Error: SOC_VERSION undefined"),
            _ok_compile_result(),
        ]
    )
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: agent,
        add_example_raw=add_example_raw,
        max_rounds=3,
    )
    update = node.func(state)
    # fix_node 不被调(LLM 绕开)
    assert agent.call_count == 0, "构建错走模板重置,fix_node 不应被调"
    # 第 2 轮 compile 跑了(re-compile 读重置后的文件)
    assert len(executor.calls) == 2
    # state.code_result.files 中 build.sh / CMakeLists.txt content 被替换
    files = (state["code_result"] or {}).get("files") or []
    by_path = {f["path"]: f["content"] for f in files}
    assert by_path["/tmp/op/CMakeLists.txt"] == add_example_raw["CMakeLists.txt"]
    assert by_path["/tmp/op/build.sh"] == add_example_raw["build.sh"]
    # 第 2 轮 success → done
    assert update["compile_fix_loop_result"]["status"] == "done"


# ---- 分流:kernel 错 → fix_node(不误杀) ----


def test_kernel_error_goes_to_fix_node():
    """stderr 无构建签名(kernel 错)→ 走 fix_node,不模板重置。"""
    add_example_raw = {"CMakeLists.txt": "SHOULD_NOT_APPEAR\n", "build.sh": "SHOULD_NOT_APPEAR\n"}
    state = _compliant_state()
    agent = _FakeAgent(response="```cpp\n// /tmp/op/op_kernel.cpp\nKERNEL_FIX\n```")
    executor = _FakeExecutor(
        [
            _fail_compile_result("syntax error in kernel at line 5"),
            _ok_compile_result(),
        ]
    )
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: agent,
        add_example_raw=add_example_raw,
        max_rounds=3,
    )
    update = node.func(state)
    # fix_node 被调
    assert agent.call_count == 1
    # 不模板重置(SHOULD_NOT_APPEAR 不应出现)
    files = (state["code_result"] or {}).get("files") or []
    by_path = {f["path"]: f["content"] for f in files}
    assert "SHOULD_NOT_APPEAR" not in by_path["/tmp/op/CMakeLists.txt"]
    assert "SHOULD_NOT_APPEAR" not in by_path["/tmp/op/build.sh"]
    # done
    assert update["compile_fix_loop_result"]["status"] == "done"


# ---- 回归:合法 kernel 错正常 fix(无 fatal 模式 + 合规 files) ----


def test_legal_kernel_fix_path_unchanged():
    """合法状态 + kernel 错 → fix_node → 第 2 轮 success,无 guard 干扰。"""
    agent = _FakeAgent(response="```cpp\n// /tmp/op/op_kernel.cpp\nKERNEL_FIX\n```")
    executor = _FakeExecutor(
        [
            _fail_compile_result("syntax error"),
            _ok_compile_result(),
        ]
    )
    node = make_real_compile_fix_loop_node(
        executor=executor,
        operator_path_resolver=_resolver("/tmp/op"),
        agent_factory=lambda: agent,
        max_rounds=3,
    )
    update = node.func(_compliant_state())
    assert agent.call_count == 1
    assert len(executor.calls) == 2
    assert update["compile_fix_loop_result"]["status"] == "done"
    assert update["compile_fix_loop_result"]["reason"] == "clean"
