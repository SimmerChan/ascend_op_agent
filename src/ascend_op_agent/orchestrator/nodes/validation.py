"""U13: 验证节点工厂(compile / precision 真节点 + fix_loop 接入)。

设计:

- ``make_real_compile_node``: 用 NpuExecutor 跑 cann_compile,产 compile_result
- ``make_real_precision_node``: 用 NpuExecutor 跑 numpy diff,产 precision_report
- ``make_compile_fix_node`` / ``make_precision_fix_node``: LLM 节点(scoped
  ascendc-crash-debug / ascendc-precision-debug skill)
- ``make_compile_fix_loop_node`` / ``make_precision_fix_loop_node``: U14 的
  make_fix_loop_node 包装,串 review→fix→re-review 循环

调用方通过 ``compile_node_factory=make_real_compile_node`` 等参数注入到
``build_new_dev_graph`` / ``build_migration_graph``,不破坏占位节点默认行为。
"""

from __future__ import annotations

from typing import Callable, Optional

from ascend_op_agent.orchestrator.fix_loop import make_fix_loop_node
from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.npu_exec import NpuExecutor
from ascend_op_agent.orchestrator.state_machine import Node


# ---- compile 节点 ----


def make_real_compile_node(
    executor: NpuExecutor,
    operator_path_resolver,
    phase: str = "compile",
) -> Node:
    """构造真实 compile 节点(用 NpuExecutor 跑 cann_compile)。

    Args:
        executor: NpuExecutor 实例
        operator_path_resolver: ``callable(state) -> str`` —— 从 state 算出
            operator_path(如从 code_result.files 提取目录)
        phase: 节点名

    Returns:
        Node —— 写 ``compile_result`` 字段
    """

    def _compile(state: dict) -> dict:
        operator_path = operator_path_resolver(state)
        result = executor.compile_to_dict(operator_path)
        return {"compile_result": result}

    return Node(name=phase, func=_compile)


# ---- precision 节点 ----


def make_real_precision_node(
    executor: NpuExecutor,
    operator_path_resolver,
    operator_name_resolver=None,
    phase: str = "precision",
    test_cases_resolver=None,  # legacy: deprecated, kept for backward compat
) -> Node:
    """构造真实 precision 节点(U4:用 NpuExecutor.run_st_driver 跑 ST 驱动)。

    6 步配方由 ST 驱动内置做(910B NPU 跑 + CPU golden + MERE/MARE 比对,
    见 2026-06-27 spike 报告),节点只需提供 operator_path 即可。

    Args:
        executor: NpuExecutor 实例
        operator_path_resolver: ``callable(state) -> str`` —— 算子工程根目录
            (含 build/custom_opp_*.run + tests/st/)
        operator_name_resolver: ``callable(state) -> str`` —— 算子名(决定
            vendors 目录 + ST 二进制名);None 时从 op_info.name 取,fallback "unknown"
        phase: 节点名
        test_cases_resolver: **deprecated**,保留仅为向后兼容(老测试用)。
            新代码不要传 —— ST 驱动自己定义 case。

    Returns:
        Node —— 写 ``precision_report`` 字段(run_st_driver 返回的同形 dict)
    """

    def _precision(state: dict) -> dict:
        operator_path = operator_path_resolver(state)
        if operator_name_resolver is not None:
            name = operator_name_resolver(state)
        else:
            name = (state.get("op_info") or {}).get("name", "unknown")
        # Gate: 编译必须成功才跑(否则 NPU run 无意义)
        compile_res = state.get("compile_result") or {}
        if not compile_res.get("success", False):
            return {
                "precision_report": {
                    "operator_name": name,
                    "total_cases": 0,
                    "passed_cases": 0,
                    "failed_cases": 0,
                    "cases": [],
                    "success": False,
                    "error": "compile_not_ready (run_st_driver 跳过)",
                }
            }
        # U4: 调 run_st_driver(NPU 跑 + CPU golden + MERE/MARE 内置)
        report = executor.run_st_driver(operator_path, op_name=name)
        return {"precision_report": report}

    return Node(name=phase, func=_precision)


# ---- fix 节点(LLM) ----


def make_compile_fix_node(
    agent_factory: AgentFactory,
    skill_bundle_text: Optional[str] = None,
    phase: str = "compile_fix",
) -> Node:
    """LLM 节点:基于 compile_result.stderr 修复代码(scoped ascendc-crash-debug)。"""
    return make_llm_node(
        phase=phase,
        task_prompt_template=(
            "你是 Ascend C 编译错误修复专家。基于 compile_result.stderr 分析错误,"
            "产出修复后的 kernel.cpp + op.cpp:\n\n"
            "状态(compile_result + code_result + 历史):\n{state}\n\n"
            "重点:语法错误、缺 header、API 误用、dtype 不匹配。\n"
            "输出修复后的完整代码;不可修复时明确说明原因。"
        ),
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )


def make_precision_fix_node(
    agent_factory: AgentFactory,
    skill_bundle_text: Optional[str] = None,
    phase: str = "precision_fix",
) -> Node:
    """LLM 节点:基于 precision_report 失败 case 修复(scoped ascendc-precision-debug)。"""
    return make_llm_node(
        phase=phase,
        task_prompt_template=(
            "你是 Ascend C 精度修复专家。基于 precision_report 失败 case 分析,"
            "产出修复后的 kernel.cpp + op.cpp:\n\n"
            "状态(precision_report + code_result + 历史):\n{state}\n\n"
            "重点:数值溢出、reduce 顺序、cast 边界、累加精度。\n"
            "输出修复后的完整代码;不可修复时明确说明原因(如缺 dtype 支持)。"
        ),
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )


# ---- fix_loop 节点 ----


def make_compile_fix_loop_node(
    review_node: Node,
    fix_node: Node,
    max_rounds: int = 3,
    phase: str = "compile_fix_loop",
) -> Node:
    """compile fix_loop 节点(U14 make_fix_loop_node 包装)。"""
    return make_fix_loop_node(
        phase=phase,
        kind="compile",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=max_rounds,
    )


def make_precision_fix_loop_node(
    review_node: Node,
    fix_node: Node,
    max_rounds: int = 3,
    phase: str = "precision_fix_loop",
) -> Node:
    """precision fix_loop 节点。"""
    return make_fix_loop_node(
        phase=phase,
        kind="precision",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=max_rounds,
    )


# ---- U2 compile fix_loop(内嵌 re-compile,U1 spike #9 后新增)----


def make_real_compile_fix_loop_node(
    executor: NpuExecutor,
    operator_path_resolver,
    agent_factory: AgentFactory,
    max_rounds: int = 5,
    skill_bundle_text: Optional[str] = None,
    sync_fn: Optional[Callable[[str], None]] = None,
    build_template_text: Optional[str] = None,
    add_example_raw: Optional[dict[str, str]] = None,
    phase: str = "compile_fix_loop",
) -> Node:
    """compile + fix 闭环(内嵌 re-compile)。

    区别于 ``make_compile_fix_loop_node``(review→fix→re-review,不含 re-compile):
    本节点每轮真跑 compile,失败时 LLM 修构建文件(markdown 落盘),re-compile。

    U1 spike #9 暴露:LLM 单次 codegen 写不对 build.sh/CMakeLists(漏 -j* case /
    ASCEND_COMPUTE_UNIT / 环境变量)。本节点通过多轮 compile→fix→re-compile 收敛。

    每轮:
      1. ``operator_path_resolver(state)`` → compile → compile_result
      2. success → done
      3. fail → fix_node(make_llm_node,看 stderr 修构建文件,markdown 自动落盘)
      4. apply_update(code_result.files 更新)→ re-compile

    Args:
        executor: NpuExecutor(SSH→910B build.sh)
        operator_path_resolver: ``callable(state) -> str`` 算子工程根目录
        agent_factory: LLM agent 工厂(修复用)
        max_rounds: 最多几轮 compile→fix
        skill_bundle_text: 修复阶段 skill 文本(ascendc-crash-debug)
        phase: 节点名

    Returns:
        Node —— 写 ``compile_result`` + ``{phase}_result``(status/rounds/reason)
    """
    from ascend_op_agent.orchestrator.state_machine import apply_update

    # fix 节点:看 compile_result.stderr,引导修构建文件(build.sh/CMakeLists)
    # + kernel。make_llm_node 自动 markdown 提取 + 落盘(common.py markdown fallback)。
    # U4:build_template_text 非空时把 add_example 模板原文 + 禁令注入 fix_node prompt。
    # 原 prompt 只提"参考 add_example 工程的正确构建配置"(模糊提示,LLM 编造 ascendc_add_ops)。
    # 现在直接内联 add_example 的 CMakeLists.txt + build.sh 原文 + 显式禁令。
    _base_prompt = (
        "你是 Ascend C 编译错误修复专家。上次编译失败,stderr 见 state.compile_result。\n\n"
        "完整状态(compile_result + code_result.files + 历史):\n{state}\n\n"
        "【关键约束 —— 构建文件已由 scaffold 正确注入,严禁修改】\n"
        "根 CMakeLists.txt / build.sh / op_host/CMakeLists.txt / op_kernel/CMakeLists.txt / "
        "op_graph/CMakeLists.txt 已由 add_example 范本参数化注入,结构正确。"
        "**严禁输出这些构建文件**。\n"
        "实测:fix_node 曾把根 CMakeLists(npu_op_package+add_subdirectory)写到 op_kernel/CMakeLists"
        "路径 → op_kernel 子目录出现 npu_op_package → 'add_custom_target cannot create target "
        "modify_vendor/gen_version_info already exists'(target 重复)→ 5 轮 max_rounds fail。\n\n"
        "【stderr 分类 → 只修语义文件 op_kernel/** + op_host/** 的 .cpp/.h】\n"
        "(构建相关 stderr 也是 kernel/host 代码引发,绝不碰构建文件)\n"
        "- fatal error: xxx.h No such file → 删该 #include(范本不 include 的是幻觉)\n"
        "- undefined/undeclared → 修 API 名/dtype\n"
        "- CMake Error target 重复 / op_kernel_sources KERNEL_FILE 缺失 → 确认"
        "op_kernel/{op}_arch22.cpp 文件名匹配,修该 .cpp,不修 CMakeLists\n\n"
        "【输出】只输出修后的**语义文件**(op_kernel/** + op_host/** 下 .cpp/.h),"
        "markdown 代码块首行 // /abs/path 或 # /abs/path。绝不输出 CMakeLists/build.sh。"
    )
    _extra = ""
    if build_template_text:
        _extra = (
            "\n\n【构建参考(add_example 真实可编译工程,U3 显式定位,910B CANN 9.1.0 兼容)】\n"
            + build_template_text
            + "\n\n【禁令 —— 必须遵守】\n"
            "- 禁止 ascendc_add_ops(CANN 9.1.0 不识别)\n"
            "- CMakeLists 必须含 npu_op_package() 宏\n"
            "- build.sh 必须保留 -j*) / --soc=*) case 与 ASCEND_COMPUTE_UNIT 透传\n"
            "- 只改必要的修复点,严禁重写整段构建文件(保留 add_example 模板结构)"
        )
    task_prompt_template = _base_prompt + _extra

    fix_node = make_llm_node(
        phase=f"{phase}_fix",
        task_prompt_template=task_prompt_template,
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
        no_tools=True,  # fix 期望 markdown 输出(common.py markdown fallback 提取修复);
        # 不禁 tool 则推理模型 tool calling 循环达 max_iterations(MAX_ITER)不产出修复
    )

    def _loop(state: dict) -> dict:
        rounds = 0
        last_result: dict = {}
        while rounds < max_rounds:
            rounds += 1

            # U5 前置扫:guard 构建文件违禁模式,命中即 fatal 早停(不浪费 compile 轮次)。
            # 计算 operator_path 在前置扫前 —— compile 也复用,避免重复调用 resolver。
            operator_path = operator_path_resolver(state)
            files = (state.get("code_result") or {}).get("files") or []
            violation = _scan_build_files_violation(files, operator_path)
            if violation:
                last_result = {
                    "success": False,
                    "return_code": 1,
                    "stdout": "",
                    "stderr": f"guard_build_template_violation:{violation}",
                    "command": "",
                    "operator_path": operator_path,
                    "soc_version": "ascend910b",
                }
                return {
                    "compile_result": last_result,
                    f"{phase}_result": {
                        "status": "failed",
                        "rounds": rounds,
                        "reason": f"guard_build_template_violation:{violation}",
                    },
                }

            # 1. compile
            last_result = executor.compile_to_dict(operator_path)
            state["compile_result"] = last_result  # 让 fix_node 看到 stderr

            if last_result.get("success"):
                return {
                    "compile_result": last_result,
                    f"{phase}_result": {
                        "status": "done",
                        "rounds": rounds,
                        "reason": "clean",
                    },
                }

            # U5 stderr fatal 早停:已知致命构建签名不浪费轮次(LLM 在这些方向上必漂)。
            stderr_fatal = _check_stderr_fatal(last_result.get("stderr", ""))
            if stderr_fatal:
                return {
                    "compile_result": last_result,
                    f"{phase}_result": {
                        "status": "failed",
                        "rounds": rounds,
                        "reason": f"guard_stderr_fatal:{stderr_fatal}",
                    },
                }

            # 2. 失败:若还有轮次,fix → re-compile
            if rounds >= max_rounds:
                break

            # U5 错误分流:构建配置错 → 模板重置(不经 LLM,绕开 LLM 在构建文件上的
            # 创造性偏差);kernel 错 → fix_node(LLM 看 stderr 修 kernel)。
            cls = _classify_stderr(last_result.get("stderr", ""))
            if cls == "build" and add_example_raw:
                _reset_build_files_in_state(state, add_example_raw, operator_path)
                _write_files_to_disk((state.get("code_result") or {}).get("files") or [])
                if sync_fn is not None:
                    sync_fn(operator_path)
                continue  # 不调 fix_node,直接下一轮 compile

            update = fix_node.func(state)
            apply_update(state, update)  # code_result.files 更新 + 磁盘落盘
            # U2:apply_update 后、下一轮 compile 前同步本地→910B,否则 fix 落盘的
            # markdown 不在远程,re-compile 读旧文件。原链路漏同步是 spike #10
            # 错误升级的部分真因(LLM 看到旧 stderr 在错位诊断上漂移)。
            if sync_fn is not None:
                sync_fn(operator_path)

        return {
            "compile_result": last_result,
            f"{phase}_result": {
                "status": "failed",
                "rounds": rounds,
                "reason": "max_rounds",
            },
        }

    return Node(name=phase, func=_loop)


# ---- U5:guard + 错误分流纯函数(便于单测,不依赖 LLM/910B)----


def _classify_stderr(stderr: str) -> str:
    """Classify stderr: 'build' (config error) vs 'kernel' (AscendC error).

    Build signatures (any hit -> build): CMake, option:, option -, -j,
    SOC_VERSION, ASCEND_CANN_PACKAGE_PATH, ASCEND_TOOLKIT_HOME, ascendc.cmake.
    Else kernel.
    """
    if not stderr:
        return "kernel"
    build_signatures = (
        "CMake",
        "option:",
        "option -",
        "-j",
        "SOC_VERSION",
        "ASCEND_CANN_PACKAGE_PATH",
        "ASCEND_TOOLKIT_HOME",
        "ascendc.cmake",
    )
    for sig in build_signatures:
        if sig in stderr:
            return "build"
    return "kernel"


def _scan_build_files_violation(code_result_files, operator_path: str = "") -> Optional[str]:
    """Pre-scan build files; return reason on violation (else None).

    CMakeLists: contains ascendc_add_ops OR missing npu_op_package( -> fatal
    build.sh: missing -j*) case -> fatal

    U5 bug fix: prefer disk (operator_path/CMakeLists.txt + build.sh) as source
    of truth -- compile reads disk, and codegen aggregation may drop entries
    from code_result.files (observed in 910B spike: CMakeLists.txt missing from
    files -> guard missed ascendc_add_ops). Fall back to code_result.files only
    when disk read is empty.
    """
    from pathlib import Path

    cmake_content = ""
    buildsh_content = ""
    if operator_path:
        cmake_p = Path(operator_path) / "CMakeLists.txt"
        if cmake_p.is_file():
            cmake_content = cmake_p.read_text(encoding="utf-8")
        buildsh_p = Path(operator_path) / "build.sh"
        if buildsh_p.is_file():
            buildsh_content = buildsh_p.read_text(encoding="utf-8")
    if not cmake_content or not buildsh_content:
        for f in code_result_files or []:
            if not isinstance(f, dict):
                continue
            path = str(f.get("path") or "")
            content = str(f.get("content") or "")
            is_root_cmake = path.endswith("/CMakeLists.txt") or path == "CMakeLists.txt"
            # 排除子目录 CMakeLists(op_host/op_kernel/op_graph/op_api 用 npu_op_kernel_sources
            # /npu_op_code_gen,不含 npu_op_package)。fallback 误匹配首个子目录 CMakeLists 会
            # 报 cmake_missing_npu_op_package 假阳 —— 实测 910B spike(spike operator_path 是
            # 远程路径,本地磁盘读失败走 fallback)files[0]=op_host/CMakeLists 触发此 bug。
            is_subdir = any(
                f"/{d}/" in path for d in ("op_host", "op_kernel", "op_graph", "op_api")
            )
            if is_root_cmake and not is_subdir:
                if not cmake_content:
                    cmake_content = content
            elif path.endswith("/build.sh") or path == "build.sh":
                if not buildsh_content:
                    buildsh_content = content
    if cmake_content:
        if "ascendc_add_ops" in cmake_content:
            return "cmake_uses_ascendc_add_ops"
        if "npu_op_package(" not in cmake_content:
            return "cmake_missing_npu_op_package"
    if buildsh_content and "-j*)" not in buildsh_content:
        return "build_sh_missing_j_case"
    return None


def _check_stderr_fatal(stderr: str) -> Optional[str]:
    """Return reason if stderr matches a known-fatal build signature (else None).

    Narrow match: ascendc_add_ops / Unknown CMake command / Unknown option: -j.
    """
    if not stderr:
        return None
    # 先匹配更具体的签名(同时含 ascendc_add_ops + Unknown CMake command 时,
    # Unknown CMake command 更能定位 LLM 在编造命令,优先报)
    if "Unknown CMake command" in stderr:
        return "stderr_unknown_cmake_command"
    if "Unknown option: -j" in stderr:
        return "stderr_unknown_option_j"
    if "ascendc_add_ops" in stderr:
        return "stderr_ascendc_add_ops"
    if "SOC_VERSION" in stderr and "not set" in stderr:
        return "stderr_soc_version_not_set"
    return None


def _reset_build_files_in_state(state: dict, add_example_raw: dict, operator_path: str) -> None:
    """Reset CMakeLists.txt + build.sh in code_result.files via add_example_raw.

    For each (fname, content): if files contains path ending /{fname}, replace
    that entry's content; else append a new entry {path: operator_path/fname,
    content, tool: template_reset}.
    """
    code_result = state.get("code_result")
    if not isinstance(code_result, dict):
        code_result = {}
        state["code_result"] = code_result
    files = list(code_result.get("files") or [])
    for fname, content in add_example_raw.items():
        replaced = False
        for f in files:
            if not isinstance(f, dict):
                continue
            p = str(f.get("path") or "")
            if p.endswith(f"/{fname}") or p == fname:
                f["content"] = content
                f["tool"] = "template_reset"
                replaced = True
                break
        if not replaced:
            files.append(
                {
                    "path": f"{operator_path.rstrip('/')}/{fname}",
                    "content": content,
                    "tool": "template_reset",
                }
            )
    code_result["files"] = files


def _write_files_to_disk(code_result_files) -> None:
    """Write file entries to disk (markdown fallback symmetry, so re-compile
    reads the new files). try/except protected for test envs without I/O.
    """
    from pathlib import Path as _P

    for f in code_result_files or []:
        if not isinstance(f, dict):
            continue
        path = str(f.get("path") or "")
        content = str(f.get("content") or "")
        if not path or not content:
            continue
        try:
            _p = _P(path)
            if _p.is_absolute():
                _p.parent.mkdir(parents=True, exist_ok=True)
                _p.write_text(content, encoding="utf-8")
        except OSError:
            pass


# ---- 便捷 resolver(从 state 提取 operator_path / test_cases) ----


def operator_path_from_code_result(state: dict) -> str:
    """默认 resolver:从 code_result.files[0].path 提取算子目录。"""
    code_result = state.get("code_result") or {}
    files = code_result.get("files") or []
    if not files:
        return ""
    first = files[0]
    if isinstance(first, dict):
        path = first.get("path") or first.get("dir") or ""
        # 取目录(算子通常是目录形式)
        return str(path)
    return str(first)


def test_cases_from_state(state: dict) -> list[dict]:
    """默认 resolver:从 state['test_cases'] 取(U13 注入或前端传入)。"""
    return list(state.get("test_cases") or [])
