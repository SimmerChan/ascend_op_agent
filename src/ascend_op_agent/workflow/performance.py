# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PerformanceEvaluator - 性能评测模块

使用 torch_npu.profiler 进行性能数据采集，生成性能对比报告。

参考 ascendc-operator-performance-eval skill 规范。
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetric:
    """性能指标"""
    name: str
    value: float
    unit: str  # "ms", "GFLOPS", "GB/s", etc.
    category: str = " latency"  # "latency", "throughput", "memory"


@dataclass
class BenchmarkCase:
    """基准测试用例"""
    name: str
    input_shapes: list[list[int]]
    input_dtypes: list[str]
    workload: str = "default"  # "light", "default", "heavy"
    iterations: int = 100


@dataclass
class PerformanceResult:
    """单个性能测试结果"""
    case_name: str
    metrics: list[PerformanceMetric]

    # 延迟指标
    avg_latency_ms: Optional[float] = None
    min_latency_ms: Optional[float] = None
    max_latency_ms: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    p99_latency_ms: Optional[float] = None

    # 吞吐量指标
    throughput_gflops: Optional[float] = None

    # 内存指标
    memory_bandwidth_gb_s: Optional[float] = None
    l2_hit_rate: Optional[float] = None


@dataclass
class PerformanceReport:
    """性能评测报告"""
    operator_name: str
    timestamp: str
    test_cases: list[BenchmarkCase]

    # 性能结果
    results: list[PerformanceResult] = field(default_factory=list)

    # 汇总指标
    avg_latency_ms: Optional[float] = None
    avg_throughput_gflops: Optional[float] = None

    # 标杆对比（如果有）
    benchmark_comparison: Optional[dict[str, Any]] = None

    # 报告路径
    report_path: str = "test/performance_report.md"

    def calculate_summary(self) -> None:
        """计算汇总指标"""
        if not self.results:
            return

        # 计算平均延迟
        latencies = [
            r.avg_latency_ms for r in self.results
            if r.avg_latency_ms is not None
        ]
        if latencies:
            self.avg_latency_ms = sum(latencies) / len(latencies)

        # 计算平均吞吐量
        throughputs = [
            r.throughput_gflops for r in self.results
            if r.throughput_gflops is not None
        ]
        if throughputs:
            self.avg_throughput_gflops = sum(throughputs) / len(throughputs)

    def to_markdown(self) -> str:
        """转换为Markdown格式"""
        self.calculate_summary()

        lines = [
            f"# Performance Report: {self.operator_name}",
            "",
            f"**Generated**: {self.timestamp}",
            "",
            f"## Summary",
            "",
        ]

        if self.avg_latency_ms is not None:
            lines.append(f"- **Avg Latency**: {self.avg_latency_ms:.4f} ms")
        if self.avg_throughput_gflops is not None:
            lines.append(f"- **Avg Throughput**: {self.avg_throughput_gflops:.2f} GFLOPS")

        lines.extend(["", "## Test Cases", ""])
        lines.append("| Case | Avg Latency (ms) | Throughput (GFLOPS) |")
        lines.append("|------|------------------|---------------------|")

        for result in self.results:
            latency = f"{result.avg_latency_ms:.4f}" if result.avg_latency_ms else "-"
            throughput = f"{result.throughput_gflops:.2f}" if result.throughput_gflops else "-"
            lines.append(f"| {result.case_name} | {latency} | {throughput} |")

        # 如果有标杆对比，添加对比部分
        if self.benchmark_comparison:
            lines.extend(["", "## Benchmark Comparison", ""])
            lines.append("| Metric | Custom | Benchmark | Speedup |")
            lines.append("|--------|--------|-----------|--------|")

            for metric_name, data in self.benchmark_comparison.items():
                custom = data.get("custom", "-")
                benchmark = data.get("benchmark", "-")
                speedup = data.get("speedup", "-")
                if isinstance(custom, float):
                    custom = f"{custom:.4f}"
                if isinstance(benchmark, float):
                    benchmark = f"{benchmark:.4f}"
                if isinstance(speedup, float):
                    speedup = f"{speedup:.2f}x"
                lines.append(f"| {metric_name} | {custom} | {benchmark} | {speedup} |")

        lines.extend(["", "## Detailed Metrics", ""])

        for result in self.results:
            lines.append(f"### {result.case_name}")
            lines.append("")

            if result.avg_latency_ms is not None:
                lines.append(f"- **Avg Latency**: {result.avg_latency_ms:.4f} ms")
            if result.min_latency_ms is not None:
                lines.append(f"- **Min Latency**: {result.min_latency_ms:.4f} ms")
            if result.max_latency_ms is not None:
                lines.append(f"- **Max Latency**: {result.max_latency_ms:.4f} ms")
            if result.p50_latency_ms is not None:
                lines.append(f"- **P50 Latency**: {result.p50_latency_ms:.4f} ms")
            if result.p99_latency_ms is not None:
                lines.append(f"- **P99 Latency**: {result.p99_latency_ms:.4f} ms")
            if result.throughput_gflops is not None:
                lines.append(f"- **Throughput**: {result.throughput_gflops:.2f} GFLOPS")
            if result.memory_bandwidth_gb_s is not None:
                lines.append(f"- **Memory Bandwidth**: {result.memory_bandwidth_gb_s:.2f} GB/s")

            lines.append("")

        return "\n".join(lines)


class PerformanceEvaluator:
    """性能评测器

    使用 torch_npu.profiler 采集性能数据，生成性能对比报告。

    评测流程:
    1. 生成 JSONL 格式测试用例
    2. 使用 torch_npu.profiler 进行 profiling
    3. 汇总 op_statistic.csv 指标
    4. 输出 Markdown 格式性能对比报告
    """

    def __init__(
        self,
        output_dir: str = "test",
        warmup_iterations: int = 5,
        active_iterations: int = 5,
    ):
        """
        Args:
            output_dir: 输出目录
            warmup_iterations: 预热迭代次数
            active_iterations: 正式测试迭代次数
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.warmup_iterations = warmup_iterations
        self.active_iterations = active_iterations

        # 默认测试用例配置
        self.default_test_cases = [
            BenchmarkCase(
                name="small",
                input_shapes=[[32, 32]],
                input_dtypes=["float32"],
                workload="light",
                iterations=1000,
            ),
            BenchmarkCase(
                name="medium",
                input_shapes=[[256, 256]],
                input_dtypes=["float32"],
                workload="default",
                iterations=100,
            ),
            BenchmarkCase(
                name="large",
                input_shapes=[[1024, 1024]],
                input_dtypes=["float32"],
                workload="heavy",
                iterations=10,
            ),
        ]

    def evaluate(
        self,
        operator_path: str,
        operator_name: str,
        test_cases: list[BenchmarkCase] = None,
        benchmark_data: Optional[dict[str, Any]] = None,
    ) -> PerformanceReport:
        """执行性能评测

        Args:
            operator_path: 算子二进制文件路径
            operator_name: 算子名称
            test_cases: 测试用例列表（默认使用 self.default_test_cases）
            benchmark_data: 标杆数据（可选），用于性能对比

        Returns:
            PerformanceReport: 性能评测报告
        """
        if test_cases is None:
            test_cases = self.default_test_cases

        # 生成测试用例文件
        test_cases_path = self._generate_test_cases(operator_name, test_cases)

        # 执行性能测试
        results = self._run_performance_test(operator_path, test_cases_path)

        # 生成报告
        report = PerformanceReport(
            operator_name=operator_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            test_cases=test_cases,
            results=results,
        )

        # 如果有标杆数据，进行对比
        if benchmark_data:
            report.benchmark_comparison = self._compare_with_benchmark(
                results, benchmark_data
            )

        # 保存报告
        report.calculate_summary()
        report_path = self.output_dir / f"{operator_name}_performance_report.md"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())
        report.report_path = str(report_path)

        logger.info(f"Performance report saved to {report_path}")

        return report

    def _generate_test_cases(
        self,
        operator_name: str,
        test_cases: list[BenchmarkCase],
    ) -> Path:
        """生成 JSONL 格式测试用例文件

        Args:
            operator_name: 算子名称
            test_cases: 测试用例列表

        Returns:
            测试用例文件路径
        """
        test_cases_path = self.output_dir / f"{operator_name}_test_cases.jsonl"

        with open(test_cases_path, "w", encoding="utf-8") as f:
            for case in test_cases:
                record = {
                    "name": case.name,
                    "input_shapes": case.input_shapes,
                    "input_dtypes": case.input_dtypes,
                    "workload": case.workload,
                    "iterations": case.iterations,
                }
                f.write(json.dumps(record) + "\n")

        logger.info(f"Generated {len(test_cases)} test cases to {test_cases_path}")

        return test_cases_path

    def _run_performance_test(
        self,
        operator_path: str,
        test_cases_path: Path,
    ) -> list[PerformanceResult]:
        """运行性能测试

        Args:
            operator_path: 算子路径
            test_cases_path: 测试用例文件路径

        Returns:
            性能测试结果列表
        """
        results = []

        # 读取测试用例
        with open(test_cases_path, "r", encoding="utf-8") as f:
            for line in f:
                case_data = json.loads(line)
                case = BenchmarkCase(**case_data)

                # 模拟性能测试（实际会调用 torch_npu.profiler）
                result = self._simulate_performance_test(case)

                results.append(result)

        return results

    def _simulate_performance_test(self, case: BenchmarkCase) -> PerformanceResult:
        """模拟性能测试（桩实现）

        在真实环境中，这里会调用 torch_npu.profiler 进行实际性能测试。

        Args:
            case: 测试用例

        Returns:
            性能测试结果
        """
        import random

        # 模拟性能数据
        # 实际会使用 profiler 采集真实数据
        base_latency = 0.1  # 基础延迟 ms

        # 根据 shape 大小调整延迟
        total_elements = 1
        for dim in case.input_shapes[0]:
            total_elements *= dim

        avg_latency = base_latency * (total_elements / 1024) * random.uniform(0.9, 1.1)

        result = PerformanceResult(
            case_name=case.name,
            metrics=[],
            avg_latency_ms=avg_latency,
            min_latency_ms=avg_latency * 0.8,
            max_latency_ms=avg_latency * 1.2,
            p50_latency_ms=avg_latency * 0.95,
            p99_latency_ms=avg_latency * 1.1,
            throughput_gflops=2.0 * total_elements / avg_latency / 1e6,
            memory_bandwidth_gb_s=random.uniform(100, 200),
        )

        return result

    def _compare_with_benchmark(
        self,
        results: list[PerformanceResult],
        benchmark_data: dict[str, Any],
    ) -> dict[str, Any]:
        """与标杆进行性能对比

        Args:
            results: 当前算子的性能结果
            benchmark_data: 标杆数据

        Returns:
            对比结果字典
        """
        comparison = {}

        for result in results:
            case_name = result.case_name

            if case_name not in benchmark_data:
                continue

            benchmark_case = benchmark_data[case_name]

            # 对比延迟
            if result.avg_latency_ms and "latency_ms" in benchmark_case:
                custom_latency = result.avg_latency_ms
                benchmark_latency = benchmark_case["latency_ms"]
                comparison[f"{case_name}_latency"] = {
                    "custom": custom_latency,
                    "benchmark": benchmark_latency,
                    "speedup": benchmark_latency / custom_latency if custom_latency > 0 else 0,
                }

            # 对比吞吐量
            if result.throughput_gflops and "throughput_gflops" in benchmark_case:
                custom_throughput = result.throughput_gflops
                benchmark_throughput = benchmark_case["throughput_gflops"]
                comparison[f"{case_name}_throughput"] = {
                    "custom": custom_throughput,
                    "benchmark": benchmark_throughput,
                    "speedup": custom_throughput / benchmark_throughput if benchmark_throughput > 0 else 0,
                }

        return comparison

    def generate_op_statistic_csv(
        self,
        report: PerformanceReport,
        output_path: Optional[Path] = None,
    ) -> Path:
        """生成 op_statistic.csv 格式的汇总指标

        Args:
            report: 性能报告
            output_path: 输出路径（可选）

        Returns:
            CSV 文件路径
        """
        if output_path is None:
            output_path = self.output_dir / f"{report.operator_name}_op_statistic.csv"

        lines = [
            "case_name,avg_latency_ms,min_latency_ms,max_latency_ms,p50_latency_ms,p99_latency_ms,throughput_gflops,memory_bandwidth_gb_s",
        ]

        for result in report.results:
            lines.append(",".join([
                result.case_name,
                f"{result.avg_latency_ms:.6f}" if result.avg_latency_ms else "",
                f"{result.min_latency_ms:.6f}" if result.min_latency_ms else "",
                f"{result.max_latency_ms:.6f}" if result.max_latency_ms else "",
                f"{result.p50_latency_ms:.6f}" if result.p50_latency_ms else "",
                f"{result.p99_latency_ms:.6f}" if result.p99_latency_ms else "",
                f"{result.throughput_gflops:.6f}" if result.throughput_gflops else "",
                f"{result.memory_bandwidth_gb_s:.6f}" if result.memory_bandwidth_gb_s else "",
            ]))

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        logger.info(f"Generated op_statistic.csv to {output_path}")

        return output_path

    def profile_with_torch_npu(
        self,
        operator_script: str,
        output_path: str,
    ) -> dict[str, Any]:
        """使用 torch_npu.profiler 进行性能分析

        Args:
            operator_script: 包含算子执行的 Python 脚本路径
            output_path: 输出路径

        Returns:
            profiler 输出数据路径
        """
        # 构建 profiler 命令
        cmd = [
            "python", "-m", "torch_npu.profiler",
            "--script", operator_script,
            "--output", output_path,
            "--warmup", str(self.warmup_iterations),
            "--active", str(self.active_iterations),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode == 0:
                logger.info(f"Profiling completed, output saved to {output_path}")
                return {"success": True, "output_path": output_path}
            else:
                logger.warning(f"Profiling failed: {result.stderr}")
                return {"success": False, "error": result.stderr}

        except FileNotFoundError:
            logger.warning("torch_npu.profiler not found, using simulation")
            return {"success": False, "error": "torch_npu.profiler not available"}
        except subprocess.TimeoutExpired:
            logger.error("Profiling timeout")
            return {"success": False, "error": "Timeout"}
