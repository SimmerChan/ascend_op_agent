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
    """cann_compile 单次调用结果。"""

    success: bool
    command: str
    stdout: str
    stderr: str
    return_code: int
    operator_path: str
    target: str


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
        target: str = "npu",
    ) -> CompileOutcome:
        """调 msopgen compile 编译算子工程。

        CANN 9.1.0 起没有独立 ``cann_compile`` 二进制,统一走
        ``msopgen compile -i <project> -q``。芯片型号(soc_version)由
        算子工程内的 ``arch_config.ini`` / CMakeLists 决定,不在命令行传。

        硬件未就绪时返回 success=False 的 outcome(不抛错)。
        根据 ``ssh_env`` 自动走本地 subprocess 或远程 SSH(可选进容器)。

        Args:
            operator_path: 算子工程根目录(含 op_host/op_kernel/CMakeLists)
            target: 保留参数(兼容旧签名),msopgen 流程不用,工程内配置决定
        """
        if self.is_remote:
            return self._compile_remote(operator_path, target)
        return self._compile_local(operator_path, target)

    def _compile_local(
        self,
        operator_path: str,
        target: str,
    ) -> CompileOutcome:
        # msopgen compile -i <project> -q(target 由工程内 soc_version 决定)
        cann_cmd = f"msopgen compile -i {operator_path} -q"
        command_str = cann_cmd

        if not operator_path or not Path(operator_path).exists():
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"operator_path not found: {operator_path}",
                return_code=2,
                operator_path=operator_path,
                target=target,
            )

        if not self.is_cann_available():
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="CANN env not configured (ASCEND_OPP_PATH / CANN_HOME missing)",
                return_code=127,
                operator_path=operator_path,
                target=target,
            )

        try:
            result = subprocess.run(
                ["bash", "-c", cann_cmd],
                capture_output=True,
                text=True,
                timeout=self.compile_timeout,
            )
            return CompileOutcome(
                success=result.returncode == 0,
                command=command_str,
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.returncode,
                operator_path=operator_path,
                target=target,
            )
        except subprocess.TimeoutExpired:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"msopgen compile timeout after {self.compile_timeout}s",
                return_code=124,
                operator_path=operator_path,
                target=target,
            )
        except FileNotFoundError:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="bash not found in PATH",
                return_code=127,
                operator_path=operator_path,
                target=target,
            )

    def _compile_remote(
        self,
        operator_path: str,
        target: str,
    ) -> CompileOutcome:
        """SSH 远程编译(可选进容器)。

        operator_path 必须是远程机器/容器内的路径。前置 ``remote_env_setup``
        加载 CANN;``container_name`` 非空时整个命令包进 docker exec。
        """
        # msopgen compile -i <project> -q(target 由工程内 soc_version 决定)
        cann_cmd = f"msopgen compile -i {operator_path} -q"
        full_cmd = self._wrap_remote_cmd(cann_cmd)
        command_str = full_cmd

        if not operator_path:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="operator_path is empty",
                return_code=2,
                operator_path=operator_path,
                target=target,
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
                target=target,
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
                target=target,
            )

        if getattr(result, "timed_out", False):
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr=f"msopgen compile timeout after {self.compile_timeout}s",
                return_code=124,
                operator_path=operator_path,
                target=target,
            )

        return CompileOutcome(
            success=result.return_code == 0,
            command=command_str,
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.return_code,
            operator_path=operator_path,
            target=target,
        )

    def compile_to_dict(self, operator_path: str, target: str = "npu") -> dict:
        """compile + 归档 + 转 dict(写 ``compile_result`` 字段格式)。"""
        outcome = self.compile(operator_path, target=target)
        result = {
            "success": outcome.success,
            "command": outcome.command,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "return_code": outcome.return_code,
            "operator_path": outcome.operator_path,
            "target": outcome.target,
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
