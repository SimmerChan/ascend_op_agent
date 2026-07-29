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

"""Performance模块单元测试

测试性能评测相关功能。
"""

import json
import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.workflow.performance import (
    PerformanceMetric,
    BenchmarkCase,
    PerformanceResult,
    PerformanceReport,
    PerformanceEvaluator,
)


class TestPerformanceMetric:
    """性能指标测试"""

    def test_metric_creation(self):
        """测试性能指标创建"""
        metric = PerformanceMetric(
            name="latency",
            value=1.5,
            unit="ms",
            category="latency",
        )

        assert metric.name == "latency"
        assert metric.value == 1.5
        assert metric.unit == "ms"
        assert metric.category == "latency"

    def test_metric_default_category(self):
        """测试默认分类"""
        metric = PerformanceMetric(
            name="throughput",
            value=100.0,
            unit="GFLOPS",
        )

        assert metric.category == " latency"  # 默认值


class TestBenchmarkCase:
    """基准测试用例测试"""

    def test_case_creation(self):
        """测试用例创建"""
        case = BenchmarkCase(
            name="small",
            input_shapes=[[32, 32]],
            input_dtypes=["float32"],
            workload="light",
            iterations=100,
        )

        assert case.name == "small"
        assert case.input_shapes == [[32, 32]]
        assert case.input_dtypes == ["float32"]
        assert case.workload == "light"
        assert case.iterations == 100

    def test_case_default_workload(self):
        """测试默认工作负载"""
        case = BenchmarkCase(
            name="default",
            input_shapes=[[64, 64]],
            input_dtypes=["float16"],
        )

        assert case.workload == "default"


class TestPerformanceResult:
    """性能结果测试"""

    def test_result_creation(self):
        """测试结果创建"""
        result = PerformanceResult(
            case_name="small",
            metrics=[],
            avg_latency_ms=1.5,
            min_latency_ms=1.2,
            max_latency_ms=1.8,
            p50_latency_ms=1.45,
            p99_latency_ms=1.75,
            throughput_gflops=100.0,
            memory_bandwidth_gb_s=150.0,
            l2_hit_rate=0.95,
        )

        assert result.case_name == "small"
        assert result.avg_latency_ms == 1.5
        assert result.throughput_gflops == 100.0
        assert result.l2_hit_rate == 0.95

    def test_result_optional_fields(self):
        """测试可选字段"""
        result = PerformanceResult(
            case_name="minimal",
            metrics=[],
        )

        assert result.avg_latency_ms is None
        assert result.throughput_gflops is None


class TestPerformanceReport:
    """性能报告测试"""

    def test_report_creation(self):
        """测试报告创建"""
        cases = [
            BenchmarkCase(name="small", input_shapes=[[32]], input_dtypes=["float32"]),
        ]
        report = PerformanceReport(
            operator_name="test_op",
            timestamp="2026-05-01 00:00:00",
            test_cases=cases,
        )

        assert report.operator_name == "test_op"
        assert len(report.test_cases) == 1

    def test_calculate_summary(self):
        """测试汇总计算"""
        results = [
            PerformanceResult(
                case_name="case1", metrics=[], avg_latency_ms=1.0, throughput_gflops=100.0
            ),
            PerformanceResult(
                case_name="case2", metrics=[], avg_latency_ms=2.0, throughput_gflops=200.0
            ),
        ]
        report = PerformanceReport(
            operator_name="test_op",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=results,
        )

        report.calculate_summary()

        assert report.avg_latency_ms == 1.5
        assert report.avg_throughput_gflops == 150.0

    def test_calculate_summary_empty_results(self):
        """测试空结果的汇总计算"""
        report = PerformanceReport(
            operator_name="test_op",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=[],
        )

        report.calculate_summary()

        assert report.avg_latency_ms is None
        assert report.avg_throughput_gflops is None

    def test_to_markdown(self):
        """测试Markdown转换"""
        results = [
            PerformanceResult(
                case_name="small",
                metrics=[],
                avg_latency_ms=1.5,
                throughput_gflops=100.0,
            ),
        ]
        report = PerformanceReport(
            operator_name="test_op",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=results,
        )

        markdown = report.to_markdown()

        assert "# Performance Report: test_op" in markdown
        assert "**Generated**: 2026-05-01 00:00:00" in markdown
        assert "| Case |" in markdown
        assert "| small |" in markdown

    def test_to_markdown_with_benchmark(self):
        """测试带标杆对比的Markdown转换"""
        results = [
            PerformanceResult(case_name="small", metrics=[], avg_latency_ms=1.5),
        ]
        report = PerformanceReport(
            operator_name="test_op",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=results,
            benchmark_comparison={
                "small_latency": {
                    "custom": 1.5,
                    "benchmark": 2.0,
                    "speedup": 1.33,
                },
            },
        )

        markdown = report.to_markdown()

        assert "## Benchmark Comparison" in markdown
        assert "| Metric |" in markdown


class TestPerformanceEvaluator:
    """性能评测器测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.evaluator = PerformanceEvaluator(output_dir=self.temp_dir)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_evaluator_creation(self):
        """测试评测器创建"""
        evaluator = PerformanceEvaluator(
            output_dir=self.temp_dir,
            warmup_iterations=10,
            active_iterations=10,
        )

        assert evaluator.warmup_iterations == 10
        assert evaluator.active_iterations == 10
        assert evaluator.output_dir == Path(self.temp_dir)

    def test_default_test_cases(self):
        """测试默认测试用例"""
        assert len(self.evaluator.default_test_cases) == 3

        names = [c.name for c in self.evaluator.default_test_cases]
        assert "small" in names
        assert "medium" in names
        assert "large" in names

    def test_evaluate_basic(self):
        """测试基本评测流程"""
        report = self.evaluator.evaluate(
            operator_path="/path/to/op",
            operator_name="test_op",
        )

        assert report.operator_name == "test_op"
        assert len(report.results) == 3
        assert report.report_path.endswith("test_op_performance_report.md")

    def test_evaluate_custom_cases(self):
        """测试自定义测试用例"""
        custom_cases = [
            BenchmarkCase(
                name="custom",
                input_shapes=[[64, 64]],
                input_dtypes=["float32"],
                workload="default",
                iterations=50,
            ),
        ]

        report = self.evaluator.evaluate(
            operator_path="/path/to/op",
            operator_name="custom_op",
            test_cases=custom_cases,
        )

        assert len(report.results) == 1
        assert report.results[0].case_name == "custom"

    def test_evaluate_with_benchmark(self):
        """测试带标杆的评测"""
        benchmark_data = {
            "small": {
                "latency_ms": 2.0,
                "throughput_gflops": 80.0,
            },
        }

        report = self.evaluator.evaluate(
            operator_path="/path/to/op",
            operator_name="benchmarked_op",
            benchmark_data=benchmark_data,
        )

        assert report.benchmark_comparison is not None
        assert "small_latency" in report.benchmark_comparison

    def test_generate_test_cases(self):
        """测试生成测试用例文件"""
        cases = [
            BenchmarkCase(
                name="test_case",
                input_shapes=[[32, 32]],
                input_dtypes=["float32"],
                workload="light",
                iterations=10,
            ),
        ]

        path = self.evaluator._generate_test_cases("test_op", cases)

        assert path.exists()
        assert path.suffix == ".jsonl"

        # 验证内容
        with open(path, "r") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["name"] == "test_case"
            assert data["input_shapes"] == [[32, 32]]

    def test_run_performance_test(self):
        """测试运行性能测试"""
        cases_path = Path(self.temp_dir) / "test_cases.jsonl"

        # 创建测试用例文件
        with open(cases_path, "w") as f:
            case = {
                "name": "perf_test",
                "input_shapes": [[128, 128]],
                "input_dtypes": ["float32"],
                "workload": "default",
                "iterations": 10,
            }
            f.write(json.dumps(case) + "\n")

        results = self.evaluator._run_performance_test("/fake/path", cases_path)

        assert len(results) == 1
        assert results[0].case_name == "perf_test"
        assert results[0].avg_latency_ms is not None

    def test_simulate_performance_test(self):
        """测试模拟性能测试"""
        case = BenchmarkCase(
            name="sim_test",
            input_shapes=[[256, 256]],
            input_dtypes=["float32"],
            workload="default",
            iterations=10,
        )

        result = self.evaluator._simulate_performance_test(case)

        assert result.case_name == "sim_test"
        assert result.avg_latency_ms > 0
        assert result.throughput_gflops > 0

    def test_compare_with_benchmark(self):
        """测试与标杆对比"""
        results = [
            PerformanceResult(
                case_name="small",
                metrics=[],
                avg_latency_ms=1.5,
                throughput_gflops=100.0,
            ),
        ]
        benchmark_data = {
            "small": {
                "latency_ms": 2.0,
                "throughput_gflops": 80.0,
            },
        }

        comparison = self.evaluator._compare_with_benchmark(results, benchmark_data)

        assert "small_latency" in comparison
        assert comparison["small_latency"]["custom"] == 1.5
        assert comparison["small_latency"]["benchmark"] == 2.0
        assert comparison["small_latency"]["speedup"] == pytest.approx(1.333, rel=0.1)

        assert "small_throughput" in comparison
        assert comparison["small_throughput"]["speedup"] == pytest.approx(1.25, rel=0.1)

    def test_compare_with_benchmark_missing_case(self):
        """测试与标杆对比（缺失case）"""
        results = [
            PerformanceResult(case_name="unknown", metrics=[], avg_latency_ms=1.5),
        ]
        benchmark_data = {
            "small": {"latency_ms": 2.0},
        }

        comparison = self.evaluator._compare_with_benchmark(results, benchmark_data)

        assert len(comparison) == 0

    def test_generate_op_statistic_csv(self):
        """测试生成CSV"""
        results = [
            PerformanceResult(
                case_name="case1",
                metrics=[],
                avg_latency_ms=1.5,
                min_latency_ms=1.2,
                max_latency_ms=1.8,
                p50_latency_ms=1.45,
                p99_latency_ms=1.75,
                throughput_gflops=100.0,
                memory_bandwidth_gb_s=150.0,
            ),
        ]
        report = PerformanceReport(
            operator_name="csv_test",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=results,
        )

        csv_path = self.evaluator.generate_op_statistic_csv(report)

        assert csv_path.exists()

        content = csv_path.read_text()
        assert "case_name" in content
        assert "case1" in content
        assert "1.5" in content
        assert "100" in content

    def test_generate_op_statistic_csv_custom_path(self):
        """测试自定义路径的CSV生成"""
        results = [
            PerformanceResult(case_name="test", metrics=[], avg_latency_ms=1.0),
        ]
        report = PerformanceReport(
            operator_name="csv_test",
            timestamp="2026-05-01 00:00:00",
            test_cases=[],
            results=results,
        )

        custom_path = Path(self.temp_dir) / "custom_path.csv"
        csv_path = self.evaluator.generate_op_statistic_csv(report, output_path=custom_path)

        assert csv_path == custom_path
        assert custom_path.exists()

    def test_profile_with_torch_npu_not_available(self):
        """测试torch_npu.profiler不可用时的处理"""
        result = self.evaluator.profile_with_torch_npu(
            operator_script="/fake/script.py",
            output_path="/fake/output",
        )

        # 由于环境中没有torch_npu.profiler，应该返回失败
        assert result["success"] is False
        assert "error" in result

    def test_report_saved_to_file(self):
        """测试报告保存到文件"""
        report = self.evaluator.evaluate(
            operator_path="/path/to/op",
            operator_name="saved_op",
        )

        report_path = Path(report.report_path)
        assert report_path.exists()

        content = report_path.read_text()
        assert "Performance Report: saved_op" in content


class TestPerformanceEvaluatorWithRealBenchmark:
    """带真实标杆数据的性能评测测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.evaluator = PerformanceEvaluator(output_dir=self.temp_dir)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_benchmark_comparison(self):
        """测试完整标杆对比"""
        benchmark_data = {
            "small": {"latency_ms": 0.5, "throughput_gflops": 50.0},
            "medium": {"latency_ms": 2.0, "throughput_gflops": 100.0},
            "large": {"latency_ms": 10.0, "throughput_gflops": 200.0},
        }

        report = self.evaluator.evaluate(
            operator_path="/path/to/op",
            operator_name="benchmark_op",
            benchmark_data=benchmark_data,
        )

        # 验证对比数据
        assert report.benchmark_comparison is not None

        # 验证所有case都有对比
        for case_name in ["small", "medium", "large"]:
            latency_key = f"{case_name}_latency"
            throughput_key = f"{case_name}_throughput"
            assert latency_key in report.benchmark_comparison
            assert throughput_key in report.benchmark_comparison

        # 验证speedup计算正确
        small_latency = report.benchmark_comparison["small_latency"]
        assert small_latency["speedup"] > 0

    def test_benchmark_speedup_calculation(self):
        """测试speedup计算"""
        results = [
            PerformanceResult(
                case_name="fast",
                metrics=[],
                avg_latency_ms=1.0,  # 比标杆快
                throughput_gflops=200.0,  # 比标杆高
            ),
        ]
        benchmark_data = {
            "fast": {
                "latency_ms": 2.0,
                "throughput_gflops": 100.0,
            },
        }

        comparison = self.evaluator._compare_with_benchmark(results, benchmark_data)

        # 延迟speedup = benchmark/custom = 2.0/1.0 = 2.0x (越低越好，所以是2.0)
        assert comparison["fast_latency"]["speedup"] == pytest.approx(2.0, rel=0.01)

        # 吞吐量speedup = custom/benchmark = 200.0/100.0 = 2.0x (越高越好)
        assert comparison["fast_throughput"]["speedup"] == pytest.approx(2.0, rel=0.01)
