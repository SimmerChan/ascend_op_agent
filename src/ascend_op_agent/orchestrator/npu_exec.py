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
    ) -> None:
        self.archive_dir = Path(archive_dir) if archive_dir is not None else None
        self.compile_timeout = compile_timeout
        self.run_timeout = run_timeout

    # ---- 环境检测 ----

    @staticmethod
    def is_cann_available() -> bool:
        """检测 CANN 环境变量是否就绪(硬件门控)。"""
        return bool(
            os.environ.get("ASCEND_OPP_PATH") or os.environ.get("CANN_HOME")
        )

    # ---- compile ----

    def compile(
        self,
        operator_path: str,
        target: str = "npu",
    ) -> CompileOutcome:
        """调 cann_compile 编译算子。

        硬件未就绪时返回 success=False 的 outcome(不抛错)。
        """
        cmd = ["cann_compile", "-target", target, operator_path]
        command_str = " ".join(cmd)

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
                cmd,
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
                stderr=f"cann_compile timeout after {self.compile_timeout}s",
                return_code=124,
                operator_path=operator_path,
                target=target,
            )
        except FileNotFoundError:
            return CompileOutcome(
                success=False,
                command=command_str,
                stdout="",
                stderr="cann_compile binary not found in PATH",
                return_code=127,
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
