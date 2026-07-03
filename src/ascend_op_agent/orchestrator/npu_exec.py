"""U13: 轻量 NPU 执行封装。

包装现有 ``agent/tools/npu_tool.py`` 的 cann_compile + msop subprocess,
返回结构化结果(CompileResult / PrecisionReport dict),便于编排器节点消费。

设计:

- ``NpuExecutor``: 封装 compile / run_precision / profile,产归档 JSON
  (``{thread_id}/{phase}_{timestamp}.json``)
- 与 ``workflow/compiler.py`` 的区别:本类只消费 cann_compile/msop CANN 工具链
  (硬件依赖),不引入 cmake/make(那是 c++ 项目流程)
- ``ASCEND_OPP_PATH`` / ``CANN_HOME`` 未设时,所有方法返回 ``success=False`` 的
  降级结果(硬件门控 —— 不抛错,让 hardware-gated 测试自动跳过真实调用)

U13 MVP 范围:compile + run_precision(numpy diff);profile/run_ut 留作后续。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np


logger = logging.getLogger(__name__)


# ---- 结果契约(dict,与 OpState 的 compile_result / precision_report 对齐) ----


@dataclass
class CompileOutcome:
    """build.sh 单次编译调用结果。"""

    success: bool
    command: str
    stdout: str
    stderr: str
    return_code: int
    operator_path: str
    soc_version: str


@dataclass
class PrecisionMetrics:
    """numpy diff 数值指标(S0-4 协议)。"""

    abs_err_max: float
    abs_err_mean: float
    rel_err_max: float
    cos_sim: float
    allclose: bool

    def meets_requirement(
        self,
        abs_err_max_threshold: float = 1e-3,
        cos_sim_threshold: float = 0.999,
    ) -> bool:
        """默认阈值:abs_err < 1e-3 且 cos_sim > 0.999。"""
        return (
            self.abs_err_max < abs_err_max_threshold
            and self.cos_sim > cos_sim_threshold
        )

    def to_dict(self) -> dict:
        return {
            "abs_err_max": float(self.abs_err_max),
            "abs_err_mean": float(self.abs_err_mean),
            "rel_err_max": float(self.rel_err_max),
            "cos_sim": float(self.cos_sim),
            "allclose": bool(self.allclose),
        }


# ---- NpuExecutor ----


class NpuExecutor:
    """封装 CANN 工具链调用。"""

    # Cosmetic check: build.sh 末尾 CPack 后 "[ERROR] Package not found or empty"
    # 是已知的 false negative(return_code=1 但 .run 产物实际已生成)。
    # 当 stdout 含 "successfully created" + ".run" → 编译实际成功。
    _COSMETIC_PASS_MARKERS = ("successfully created", ".run")
    """封装 CANN 工具链调用。

    Args:
        archive_dir: 结果归档目录;None 时不归档
        compile_timeout: cann_compile 超时秒
        run_timeout: 算子执行超时秒
    """

    def __init__(
        self,
        archive_dir: Optional[Path | str] = None,
        compile_timeout: int = 300,
        run_timeout: int = 60,
        ssh_env: Optional[Any] = None,
        remote_env_setup: str = "",
        container_name: str = "",
    ) -> None:
        """
        Args:
            archive_dir: 结果归档目录;None 时不归档
            compile_timeout: msopgen compile 超时秒
            run_timeout: 算子执行超时秒
            ssh_env: SSHEnvironment 实例(远程执行);None 时本地 subprocess。
                远程模式下 operator_path 必须是远程机器(或容器内)的路径。
            remote_env_setup: 远程命令前缀(如 ``"source /usr/local/Ascend/
                ascend-toolkit/set_env.sh && "``),用于在非交互 SSH 会话里
                加载 CANN 环境变量。本地模式忽略。
            container_name: 远程开发容器名(如 ``"ops_pt"``);非空时远程命令
                自动包装 ``docker exec <container> bash -c "..."``。本地忽略。
                ssh_env 直连容器(容器内跑 sshd)时留空。
        """
        self.archive_dir = Path(archive_dir) if archive_dir is not None else None
        self.compile_timeout = compile_timeout
        self.run_timeout = run_timeout
        self.ssh_env = ssh_env
        self.remote_env_setup = remote_env_setup or ""
        self.container_name = container_name or ""

    @property
    def is_remote(self) -> bool:
        """是否走 SSH 远程执行。"""
        return self.ssh_env is not None

    @property
    def is_containerized(self) -> bool:
        """远程是否走 docker exec 进容器。"""
        return bool(self.container_name)

    @staticmethod
    def _is_compile_success(return_code: int, stdout: str) -> bool:
        """compile 成功判定(含 cosmetic 修正)。

        - return_code == 0 → True(正常成功)
        - return_code == 1 且 stdout 含 cosmetic markers → True
          (build.sh 末尾 CPack check false negative, .run 产物实际已生成)
        - 其他 → False
        """
        if return_code == 0:
            return True
        if return_code == 1 and stdout:
            return all(m in stdout for m in NpuExecutor._COSMETIC_PASS_MARKERS)
        return False

    # ---- 命令包装 ----

    def _wrap_remote_cmd(self, inner_cmd: str) -> str:
        """包装远程命令:加 remote_env_setup 前缀 + 可选 docker exec 外壳。

        - inner_cmd: 实际要跑的命令(如 ``msopgen compile -i /path -q``)
        - 返回:含 source env + (可选)docker exec 的完整命令串
        """
        full = f"{self.remote_env_setup}{inner_cmd}"
        if self.is_containerized:
            # docker exec 里再起 bash -c,内部用单引号包(避免与外层 SSH 引号冲突)
            # 注意:inner_cmd 内不应含单引号;调用方需自行转义
            full = f"docker exec {self.container_name} bash -c '{full}'"
        return full

    # ---- 环境检测 ----

    @staticmethod
    def is_cann_available() -> bool:
        """检测本地 CANN 环境变量是否就绪(硬件门控)。

        远程模式请用 ``is_remote_cann_available``。
        """
        return bool(
            os.environ.get("ASCEND_OPP_PATH") or os.environ.get("CANN_HOME")
        )

    def is_remote_cann_available(self) -> bool:
        """远程模式:SSH(可选进容器)探测 ASCEND_OPP_PATH 是否就绪。

        非交互 SSH 会话默认不加载 CANN env,需靠 ``remote_env_setup`` 前缀
        source 工具链脚本。
        """
        if self.ssh_env is None:
            return False
        cmd = self._wrap_remote_cmd("echo $ASCEND_OPP_PATH")
        try:
            result = self.ssh_env.execute(cmd, timeout=15)
        except Exception as e:
            logger.warning(f"is_remote_cann_available probe failed: {e}")
            return False
        out = (result.stdout or "").strip()
        # 未 source 时,远程 bash 可能原样回显 "$ASCEND_OPP_PATH" 或空
        return bool(out) and out != "$ASCEND_OPP_PATH"

    # ---- compile ----

    def compile(
        self,
        operator_path: str,
        soc_version: str = "ascend910b",
        cores: int = 8,
    ) -> CompileOutcome:
        """调 ``bash build.sh --soc=<soc_version>`` 编译 AscendC 算子工程。

        AscendC 新式工程(op_host/op_kernel + CMakeLists + build.sh)的编译入口
        是工程自带的 ``build.sh``(内部封装 cmake + opc)。芯片型号通过
        ``--soc`` 参数传(910B3 → ``ascend910b``;A5/950 → ``ascend950``;
        910A → ``ascend910a``)。非 AscendC 老式 TBE 工程才用 msopgen compile。

        硬件未就绪时返回 success=False 的 outcome(不抛错)。
        根据 ``ssh_env`` 自动走本地 subprocess 或远程 SSH(可选进容器)。

        Args:
            operator_path: 算子工程根目录(含 build.sh/op_host/op_kernel)
            soc_version: 目标芯片型号(默认 ``ascend910b`` 对应 910B3)
            cores: 编译线程数(build.sh -j 参数)
        """
        if self.is_remote:
            return self._compile_remote(operator_path, soc_version, cores)
        return self._compile_local(operator_path, soc_version, cores)

    @staticmethod
    def _build_compile_cmd(
        operator_path: str,
        soc_version: str,
        cores: int,
    ) -> str:
        """构造编译命令:cd 进工程 + bash build.sh --soc=<soc> -j<n>。"""
        return f"cd {operator_path} && bash build.sh --soc={soc_version} -j{cores}"

    def _compile_local(
        self,
        operator_path: str,
        soc_version: str,
        cores: int,
    ) -> CompileOutcome:
        compile_cmd = self._build_compile_cmd(operator_path, soc_version, cores)
        command_str = compile_cmd

        if not operator_path or not Path(operator_path).exists():
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"operator_path not found: {operator_path}",
                return_code=2,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        if not self.is_cann_available():
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="CANN env not configured (ASCEND_OPP_PATH / CANN_HOME missing)",
                return_code=127,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        try:
            result = subprocess.run(
                ["bash", "-c", compile_cmd],
                capture_output=True,
                text=True,
                timeout=self.compile_timeout,
            )
            return CompileOutcome(
                success=self._is_compile_success(result.returncode, result.stdout or ""),
                command=command_str,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
                operator_path=operator_path,
                soc_version=soc_version,
            )
        except subprocess.TimeoutExpired:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"build.sh timeout after {self.compile_timeout}s",
                return_code=124,
                operator_path=operator_path,
                soc_version=soc_version,
            )
        except FileNotFoundError:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="bash not found in PATH",
                return_code=127,
                operator_path=operator_path,
                soc_version=soc_version,
            )

    def _compile_remote(
        self,
        operator_path: str,
        soc_version: str,
        cores: int,
    ) -> CompileOutcome:
        """SSH 远程编译(可选进容器)。

        operator_path 必须是远程机器/容器内的路径。前置 ``remote_env_setup``
        加载 CANN;``container_name`` 非空时整个命令包进 docker exec。
        """
        compile_cmd = self._build_compile_cmd(operator_path, soc_version, cores)
        full_cmd = self._wrap_remote_cmd(compile_cmd)
        command_str = full_cmd

        if not operator_path:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="operator_path is empty",
                return_code=2,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        if not self.is_remote_cann_available():
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=(
                    "remote CANN env not configured "
                    "(remote_env_setup 未 source 或 ASCEND_OPP_PATH 缺失)"
                ),
                return_code=127,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        try:
            result = self.ssh_env.execute(  # type: ignore[union-attr]
                full_cmd, timeout=self.compile_timeout
            )
        except Exception as e:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"SSH execute failed: {e}",
                return_code=126,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        if getattr(result, "timed_out", False):
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"msopgen compile timeout after {self.compile_timeout}s",
                return_code=124,
                operator_path=operator_path,
                soc_version=soc_version,
            )

        return CompileOutcome(
            success=self._is_compile_success(result.return_code, result.stdout or ""),
            command=command_str,
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.return_code,
            operator_path=operator_path,
            soc_version=soc_version,
        )

    def compile_to_dict(
        self,
        operator_path: str,
        soc_version: str = "ascend910b",
        cores: int = 8,
    ) -> dict:
        """compile + 归档 + 转 dict(写 ``compile_result`` 字段格式)。"""
        outcome = self.compile(operator_path, soc_version=soc_version, cores=cores)
        result = {
            "success": outcome.success,
            "command": outcome.command,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "return_code": outcome.return_code,
            "operator_path": outcome.operator_path,
            "soc_version": outcome.soc_version,
        }
        self._archive("compile", result)
        return result

    # ---- run_precision(numpy diff) ----

    @staticmethod
    def compute_precision_metrics(
        golden: np.ndarray,
        actual: np.ndarray,
    ) -> PrecisionMetrics:
        """S0-4 协议:计算 golden vs actual 的数值差异。

        两个数组需 shape 兼容(否则 ValueError)。
        """
        if golden.shape != actual.shape:
            raise ValueError(
                f"shape mismatch: golden={golden.shape} vs actual={actual.shape}"
            )
        diff = np.abs(golden.astype(np.float64) - actual.astype(np.float64))
        abs_err_max = float(np.max(diff))
        abs_err_mean = float(np.mean(diff))
        # rel_err 避免 div by zero
        golden_abs = np.abs(golden.astype(np.float64))
        rel_err = np.where(
            golden_abs > 1e-12,
            diff / np.maximum(golden_abs, 1e-12),
            np.zeros_like(diff),
        )
        rel_err_max = float(np.max(rel_err))
        # cos_sim
        g_flat = golden.astype(np.float64).flatten()
        a_flat = actual.astype(np.float64).flatten()
        norm_g = np.linalg.norm(g_flat)
        norm_a = np.linalg.norm(a_flat)
        if norm_g < 1e-12 or norm_a < 1e-12:
            cos_sim = 0.0
        else:
            cos_sim = float(np.dot(g_flat, a_flat) / (norm_g * norm_a))
        # allclose(默认 rtol=1e-5, atol=1e-8)
        allclose = bool(np.allclose(golden, actual, rtol=1e-5, atol=1e-8))
        return PrecisionMetrics(
            abs_err_max=abs_err_max,
            abs_err_mean=abs_err_mean,
            rel_err_max=rel_err_max,
            cos_sim=cos_sim,
            allclose=allclose,
        )

    def run_precision(
        self,
        operator_name: str,
        test_cases: list[dict],
    ) -> dict:
        """对 N 个 test_case 跑 numpy diff,产出 PrecisionReport dict。

        Args:
            operator_name: 算子名(写报告)
            test_cases: ``[{"golden": np.ndarray, "actual": np.ndarray}, ...]``

        Returns:
            ``precision_report`` 字段格式 dict(operator_name / total_cases /
            passed_cases / failed_cases / cases: [...])
        """
        cases_out: list[dict] = []
        passed = 0
        for i, tc in enumerate(test_cases):
            try:
                golden = np.asarray(tc["golden"])
                actual = np.asarray(tc["actual"])
                metrics = self.compute_precision_metrics(golden, actual)
                ok = metrics.meets_requirement()
                cases_out.append(
                    {
                        "case_id": i,
                        "passed": ok,
                        "metrics": metrics.to_dict(),
                    }
                )
                if ok:
                    passed += 1
            except (KeyError, ValueError) as e:
                cases_out.append(
                    {
                        "case_id": i,
                        "passed": False,
                        "error": str(e),
                    }
                )

        report = {
            "operator_name": operator_name,
            "total_cases": len(test_cases),
            "passed_cases": passed,
            "failed_cases": len(test_cases) - passed,
            "cases": cases_out,
        }
        self._archive("precision", report)
        return report

    # ---- run_st_driver(U1, 基于 2026-06-27 spike 报告) ----
    #
    # spike 发现 CANN 9.1.0 不存在 msoput/test_main;真实路径是 scaffold 自带的
    # 手写 C++ aclnn ST 驱动(tests/st/test_aclnn_<op>.cpp),驱动内部做 NPU 跑 +
    # CPU golden + MERE/MARE 比对。run_st_driver 编排 install→build→run→parse,
    # 返回与 run_precision 同形的 PrecisionReport dict(可直接写 precision_node)。

    # ST 驱动 stdout 正则(spike 实测样本,见 docs/e2e/2026-06-27-st-driver-spike-report.md)
    _ST_FP_CASE_RE = re.compile(
        r"\[(PASS|FAIL)\]\s+MERE=([\d.eE+-]+),\s*MARE=([\d.eE+-]+)"
        r"\s*\(threshold=([\d.eE+-]+),\s*(\d+)\s*elems\)"
    )
    _ST_INT_CASE_RE = re.compile(
        r"\[(PASS|FAIL)\]\s+所有\s*(\d+)\s*个元素一致"
    )
    _ST_SUMMARY_RE = {
        "total": re.compile(r"总计:\s*(\d+)"),
        "passed": re.compile(r"通过:\s*(\d+)"),
        "failed": re.compile(r"失败:\s*(\d+)"),
    }

    def run_st_driver(
        self,
        operator_path: str,
        op_name: str = "add_example",
        soc_version: str = "ascend910b",
        st_subdir: str = "tests/st",
    ) -> dict:
        """跑 scaffold 自带的 C++ aclnn ST 驱动,返回 PrecisionReport dict。

        6 步配方(spike 实测通过):
          1. install op 包(--force,见 spike 坑 2)
          2. set LD_LIBRARY_PATH(spike 坑 3)
          3. cmake + make ST 驱动
          4. 跑驱动二进制
          5. 解析 stdout MERE/MARE + 总计/通过/失败

        Args:
            operator_path: 算子工程根目录(含 build/custom_opp_*.run + tests/st/)
            op_name: 算子名(决定 vendors 目录 + ST 二进制名)
            soc_version: 芯片型号(传给 cmake,默认 ascend910b)
            st_subdir: ST 驱动源码相对目录(默认 tests/st)

        Returns:
            PrecisionReport dict(operator_name/total_cases/passed_cases/
            failed_cases/cases) —— 与 run_precision 同形,可直接写 precision_node。
            失败时 success=False 的降级报告(不抛)。
        """
        if self.is_remote:
            return self._run_st_driver_remote(
                operator_path, op_name, soc_version, st_subdir
            )
        return self._run_st_driver_local(
            operator_path, op_name, soc_version, st_subdir
        )

    @staticmethod
    def _parse_st_stdout(stdout: str, op_name: str) -> dict:
        """解析 ST 驱动 stdout,产 PrecisionReport dict。

        stdout 锚点(spike 实测):
          FP case: `  [PASS] MERE=0.00e+00, MARE=0.00e+00 (threshold=1.22e-04, 6 elems)`
          INT case:`  [PASS] 所有 6 个元素一致`
          summary: `总计: 10` / `通过: 10` / `失败: 0`
        """
        lines = (stdout or "").splitlines()
        cases: list[dict] = []
        case_id = 0
        for line in lines:
            m = NpuExecutor._ST_FP_CASE_RE.search(line)
            if m:
                status, mere, mare, thr, elems = m.groups()
                cases.append({
                    "case_id": case_id,
                    "passed": status == "PASS",
                    "metrics": {
                        "mere": float(mere),
                        "mare": float(mare),
                        "threshold": float(thr),
                        "elems": int(elems),
                    },
                })
                case_id += 1
                continue
            mi = NpuExecutor._ST_INT_CASE_RE.search(line)
            if mi:
                status, elems = mi.groups()
                cases.append({
                    "case_id": case_id,
                    "passed": status == "PASS",
                    "metrics": {"elems": int(elems), "dtype": "int"},
                })
                case_id += 1

        total = passed = failed = None
        for line in lines:
            if total is None:
                mt = NpuExecutor._ST_SUMMARY_RE["total"].search(line)
                if mt:
                    total = int(mt.group(1))
            if passed is None:
                mp = NpuExecutor._ST_SUMMARY_RE["passed"].search(line)
                if mp:
                    passed = int(mp.group(1))
            if failed is None:
                mf = NpuExecutor._ST_SUMMARY_RE["failed"].search(line)
                if mf:
                    failed = int(mf.group(1))

        # summary 缺失时从 cases 推
        if total is None:
            total = len(cases)
        if passed is None:
            passed = sum(1 for c in cases if c.get("passed"))
        if failed is None:
            failed = total - passed

        return {
            "operator_name": op_name,
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "cases": cases,
        }

    def _run_st_driver_local(
        self, operator_path: str, op_name: str, soc_version: str, st_subdir: str
    ) -> dict:
        """本地模式跑 ST 驱动(开发机无 CANN 时返回降级报告)。"""
        if not self.is_cann_available():
            return self._st_driver_failure(
                op_name, "CANN env not configured (ASCEND_OPP_PATH / CANN_HOME missing)"
            )
        return self._run_st_driver_recipe(
            operator_path, op_name, soc_version, st_subdir, remote=False
        )

    def _run_st_driver_remote(
        self, operator_path: str, op_name: str, soc_version: str, st_subdir: str
    ) -> dict:
        """SSH 远程(可选进容器)跑 ST 驱动。"""
        if not operator_path:
            return self._st_driver_failure(op_name, "operator_path is empty")
        if not self.is_remote_cann_available():
            return self._st_driver_failure(
                op_name,
                "remote CANN env not configured (remote_env_setup 未 source 或 ASCEND_OPP_PATH 缺失)",
            )
        return self._run_st_driver_recipe(
            operator_path, op_name, soc_version, st_subdir, remote=True
        )

    def _run_st_driver_recipe(
        self,
        operator_path: str,
        op_name: str,
        soc_version: str,
        st_subdir: str,
        remote: bool,
    ) -> dict:
        """6 步配方的实际执行(install→build→run→parse),本地/远程共用。

        每步用独立的 subprocess/SSH 调用,失败清晰归因(不混在一个 compound 命令里)。
        """
        run_path = f"{operator_path}/{st_subdir}/build_st"

        def _exec(cmd: str) -> tuple[bool, str, str, int]:
            """跑一条命令,返回 (success, stdout, stderr, returncode)。"""
            try:
                if remote:
                    full = self._wrap_remote_cmd(cmd)  # type: ignore[union-attr]
                    r = self.ssh_env.execute(full, timeout=self.run_timeout)  # type: ignore[union-attr]
                    rc = r.return_code
                    return (rc == 0 and not getattr(r, "timed_out", False),
                            r.stdout or "", r.stderr or "", rc)
                else:
                    r = subprocess.run(["bash", "-c", cmd],
                                       capture_output=True, text=True,
                                       timeout=self.run_timeout)
                    return (r.returncode == 0, r.stdout, r.stderr, r.returncode)
            except Exception as e:
                logger.warning(f"run_st_driver exec failed: {e}")
                return (False, "", str(e), 126)

        # Step 1: install op 包(--force,见 spike 坑 2)
        install_cmd = (
            f"cd {operator_path}/build && "
            f"./custom_opp_almalinux_aarch64.run --force"
        )
        ok, out, err, rc = _exec(install_cmd)
        if not ok:
            return self._st_driver_failure(
                op_name, f"install op package failed (rc={rc}): {err[:300]}"
            )

        # Step 2+3: build ST 驱动(cmake + make),用 ASCEND_HOME_PATH(spike 坑 3)
        vendors_lib = f"$ASCEND_HOME_PATH/opp/vendors/{op_name}_custom/op_api/lib"
        build_cmd = (
            f"cd {operator_path}/{st_subdir} && rm -rf build_st && mkdir build_st "
            f"&& cd build_st && cmake .. && make -j4"
        )
        ok, out, err, rc = _exec(build_cmd)
        if not ok:
            return self._st_driver_failure(
                op_name, f"build ST driver failed (rc={rc}): {err[:300]}"
            )

        # Step 4: 跑 ST 二进制(设 LD_LIBRARY_PATH,spike 坑 3)
        binary = f"{run_path}/test_aclnn_{op_name}"
        run_cmd = (
            f"export LD_LIBRARY_PATH={vendors_lib}:$LD_LIBRARY_PATH && "
            f"{binary}"
        )
        ok, out, err, rc = _exec(run_cmd)

        # Step 5: 解析 stdout(即使 ok=False 也尝试解析 —— 二进制可能 rc!=0 但有部分输出)
        report = self._parse_st_stdout(out, op_name)
        report["return_code"] = rc
        report["success"] = ok and report["passed_cases"] == report["total_cases"]
        report.setdefault("stderr", err[:500] if err else "")
        self._archive("precision", report)
        return report

    @staticmethod
    def _st_driver_failure(op_name: str, reason: str) -> dict:
        """ST 驱动失败的降级报告(与 run_precision 同形,便于节点消费)。"""
        report = {
            "operator_name": op_name,
            "total_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
            "cases": [],
            "success": False,
            "error": reason,
        }
        return report

    # ---- 归档 ----

    def _archive(self, phase: str, payload: dict) -> None:
        if self.archive_dir is None:
            return
        try:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            outfile = self.archive_dir / f"{phase}_{ts}.json"
            with open(outfile, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"NpuExecutor archived {phase} → {outfile}")
        except Exception as e:
            logger.warning(f"NpuExecutor archive failed: {e}")
