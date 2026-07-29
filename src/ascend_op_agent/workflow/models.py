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

"""Workflow数据模型"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MigrationStrategy(Enum):
    """迁移策略"""

    FROM_SCRATCH = "from_scratch"
    CUDA_TO_ASCENDC = "cuda_to_ascendc"
    TRITON_TO_ASCENDC = "triton_to_ascendc"
    CUTLASS_TO_ASCENDC = "cutlass_to_ascendc"


class PhaseStatus(Enum):
    """阶段状态"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ArchitectureMapping:
    """GPU迁移架构映射"""

    source_type: str  # "cuda", "triton", "cutlass"
    mappings: dict[str, str] = field(default_factory=dict)

    # CUDA -> AscendC 映射
    CUDA_MAPPINGS = {
        "shared_memory": "Global Memory",
        "thread": "Tiling",
        "block": "Cluster",
        "grid": "Device",
        "std::vector": "Tensor",
        "cudaMalloc": "AllocTensor",
        "cudaMemcpy": "CopyTensor",
        "__global__": "OCL_KERNEL",
        "__shared__": "__attribute__((aligned(128)))",
        "threadIdx.x": "get_local_id(0)",
        "blockIdx.x": "get_group_id(0)",
    }

    # Triton -> AscendC 映射
    TRITON_MAPPINGS = {
        "tl.load": "LoadTensor",
        "tl.store": "StoreTensor",
        "triton.jit": "AscendC Kernel",
        "tl.constexpr": "constexpr",
        "tl.num_warps": "num_worker",
        "tl.sum": "ReduceSum",
        "tl.max": "ReduceMax",
    }

    @classmethod
    def get_mapping(cls, source_type: str) -> "ArchitectureMapping":
        """获取指定源类型的架构映射"""
        if source_type == "cuda":
            return cls(source_type=source_type, mappings=cls.CUDA_MAPPINGS)
        elif source_type == "triton":
            return cls(source_type=source_type, mappings=cls.TRITON_MAPPINGS)
        elif source_type == "cutlass":
            # CUTLASS 基于 CUDA，扩展映射
            mappings = dict(cls.CUDA_MAPPINGS)
            mappings.update(
                {
                    "cutlass::MatrixCoord": "Coord<2>",
                    "cutlass::gemm::GemmCoord": "Coord<3>",
                }
            )
            return cls(source_type=source_type, mappings=mappings)
        return cls(source_type=source_type, mappings={})

    def to_dict(self) -> dict:
        """序列化(只含实例字段 source_type+mappings,类变量 CUDA_MAPPINGS 等不进 dict)。"""
        return {"source_type": self.source_type, "mappings": dict(self.mappings)}

    @classmethod
    def from_dict(cls, data: dict) -> "ArchitectureMapping":
        return cls(
            source_type=data["source_type"],
            mappings=dict(data.get("mappings", {})),
        )


@dataclass
class OpInfo:
    """算子信息"""

    name: str
    description: str
    op_type: str  # "elementwise", "matmul", "reduction", "conv", etc.

    # 输入输出信息
    input_shapes: list[list[int]] = field(default_factory=list)
    input_dtypes: list[str] = field(default_factory=list)
    output_shapes: list[list[int]] = field(default_factory=list)
    output_dtypes: list[str] = field(default_factory=list)

    # 迁移相关
    migration_strategy: MigrationStrategy = MigrationStrategy.FROM_SCRATCH
    ref_code_path: Optional[str] = None  # GPU参考代码路径
    ref_code_type: Optional[str] = None  # "cuda", "triton", "cutlass"

    # 额外属性
    attributes: dict[str, Any] = field(default_factory=dict)

    def detect_migration_scenario(self) -> bool:
        """检测是否为GPU迁移场景"""
        return self.ref_code_path is not None and self.ref_code_type in (
            "cuda",
            "triton",
            "cutlass",
        )

    def get_complexity_score(self) -> int:
        """评估算子复杂度（1-10）"""
        score = 1

        # 基于算子类型
        if self.op_type in ("matmul", "conv"):
            score += 3
        elif self.op_type in ("reduction", "softmax"):
            score += 2
        else:
            score += 1

        # 基于输入维度
        for shape in self.input_shapes:
            if len(shape) > 4:
                score += 2
            elif len(shape) > 2:
                score += 1

        # 基于数据类型
        if "int" in str(self.input_dtypes):
            score += 1

        return min(score, 10)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "op_type": self.op_type,
            "input_shapes": [list(s) for s in self.input_shapes],
            "input_dtypes": list(self.input_dtypes),
            "output_shapes": [list(s) for s in self.output_shapes],
            "output_dtypes": list(self.output_dtypes),
            "migration_strategy": self.migration_strategy.value,
            "ref_code_path": self.ref_code_path,
            "ref_code_type": self.ref_code_type,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OpInfo":
        ms_raw = data.get("migration_strategy")
        ms = MigrationStrategy(ms_raw) if ms_raw else MigrationStrategy.FROM_SCRATCH
        return cls(
            name=data["name"],
            description=data["description"],
            op_type=data["op_type"],
            input_shapes=[list(s) for s in data.get("input_shapes", [])],
            input_dtypes=list(data.get("input_dtypes", [])),
            output_shapes=[list(s) for s in data.get("output_shapes", [])],
            output_dtypes=list(data.get("output_dtypes", [])),
            migration_strategy=ms,
            ref_code_path=data.get("ref_code_path"),
            ref_code_type=data.get("ref_code_type"),
            attributes=dict(data.get("attributes", {})),
        )


@dataclass
class DesignDoc:
    """方案设计文档"""

    op_info: OpInfo

    # 内存布局
    input_layouts: list[str] = field(
        default_factory=list
    )  # "ROW_MAJOR", "COL_MAJOR", "NCHW", "NHWC"
    output_layouts: list[str] = field(default_factory=list)

    # Tiling策略
    tile_shape: Optional[list[int]] = None
    block_dim: Optional[list[int]] = None

    # 架构映射（GPU迁移场景）
    arch_mapping: Optional[ArchitectureMapping] = None

    # 设计决策
    decisions: list[str] = field(default_factory=list)

    # 用户确认状态
    confirmed: bool = False

    def to_dict(self) -> dict:
        """递归序列化(含 op_info + arch_mapping)。"""
        return {
            "op_info": self.op_info.to_dict(),
            "input_layouts": list(self.input_layouts),
            "output_layouts": list(self.output_layouts),
            "tile_shape": list(self.tile_shape) if self.tile_shape else None,
            "block_dim": list(self.block_dim) if self.block_dim else None,
            "arch_mapping": self.arch_mapping.to_dict() if self.arch_mapping else None,
            "decisions": list(self.decisions),
            "confirmed": self.confirmed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DesignDoc":
        am_raw = data.get("arch_mapping")
        return cls(
            op_info=OpInfo.from_dict(data["op_info"]),
            input_layouts=list(data.get("input_layouts", [])),
            output_layouts=list(data.get("output_layouts", [])),
            tile_shape=list(data["tile_shape"]) if data.get("tile_shape") else None,
            block_dim=list(data["block_dim"]) if data.get("block_dim") else None,
            arch_mapping=ArchitectureMapping.from_dict(am_raw) if am_raw else None,
            decisions=list(data.get("decisions", [])),
            confirmed=bool(data.get("confirmed", False)),
        )


@dataclass
class FileChange:
    """文件变更"""

    path: str
    action: str  # "create", "modify", "delete"
    content: Optional[str] = None

    def to_dict(self) -> dict:
        return {"path": self.path, "action": self.action, "content": self.content}

    @classmethod
    def from_dict(cls, data: dict) -> "FileChange":
        return cls(
            path=data["path"],
            action=data["action"],
            content=data.get("content"),
        )


@dataclass
class CodeGenResult:
    """代码生成结果"""

    success: bool
    files: list[FileChange] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # 生成的文件路径（相对于workspace）
    generated_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典(含 files[*].content,可 round-trip)。"""
        return {
            "success": self.success,
            "files": [f.to_dict() for f in self.files],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "generated_files": list(self.generated_files),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CodeGenResult":
        return cls(
            success=bool(data["success"]),
            files=[FileChange.from_dict(f) for f in data.get("files", [])],
            errors=list(data.get("errors", [])),
            warnings=list(data.get("warnings", [])),
            generated_files=list(data.get("generated_files", [])),
        )


@dataclass
class CompileResult:
    """编译验证结果"""

    success: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0

    # 修复尝试
    fix_attempts: int = 0
    fixed: bool = False

    # 错误类型分类
    syntax_errors: list[str] = field(default_factory=list)
    missing_headers: list[str] = field(default_factory=list)
    type_errors: list[str] = field(default_factory=list)
    other_errors: list[str] = field(default_factory=list)

    def can_fix(self) -> bool:
        """是否可以通过确定性修复解决"""
        return len(self.syntax_errors) > 0 or len(self.missing_headers) > 0

    def should_stop_fixing(self) -> bool:
        """是否应该停止修复（已达最大次数）"""
        return self.fix_attempts >= 3

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_code": self.return_code,
            "fix_attempts": self.fix_attempts,
            "fixed": self.fixed,
            "syntax_errors": list(self.syntax_errors),
            "missing_headers": list(self.missing_headers),
            "type_errors": list(self.type_errors),
            "other_errors": list(self.other_errors),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompileResult":
        return cls(
            success=bool(data["success"]),
            command=data["command"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            return_code=int(data.get("return_code", 0)),
            fix_attempts=int(data.get("fix_attempts", 0)),
            fixed=bool(data.get("fixed", False)),
            syntax_errors=list(data.get("syntax_errors", [])),
            missing_headers=list(data.get("missing_headers", [])),
            type_errors=list(data.get("type_errors", [])),
            other_errors=list(data.get("other_errors", [])),
        )


@dataclass
class TestCase:
    """测试用例"""

    name: str
    input_shapes: list[list[int]]
    input_dtypes: list[str]
    expected_shapes: list[list[int]]
    expected_dtypes: list[str]
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "input_shapes": [list(s) for s in self.input_shapes],
            "input_dtypes": list(self.input_dtypes),
            "expected_shapes": [list(s) for s in self.expected_shapes],
            "expected_dtypes": list(self.expected_dtypes),
            "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestCase":
        return cls(
            name=data["name"],
            input_shapes=[list(s) for s in data["input_shapes"]],
            input_dtypes=list(data["input_dtypes"]),
            expected_shapes=[list(s) for s in data["expected_shapes"]],
            expected_dtypes=list(data["expected_dtypes"]),
            attrs=dict(data.get("attrs", {})),
        )


@dataclass
class TestResult:
    """单个测试结果"""

    test_case: TestCase
    passed: bool
    abs_err: Optional[float] = None
    rel_err: Optional[float] = None
    cos_sim: Optional[float] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "test_case": self.test_case.to_dict(),
            "passed": self.passed,
            "abs_err": self.abs_err,
            "rel_err": self.rel_err,
            "cos_sim": self.cos_sim,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TestResult":
        return cls(
            test_case=TestCase.from_dict(data["test_case"]),
            passed=bool(data["passed"]),
            abs_err=data.get("abs_err"),
            rel_err=data.get("rel_err"),
            cos_sim=data.get("cos_sim"),
            error_message=data.get("error_message"),
        )


@dataclass
class PrecisionReport:
    """精度评估报告"""

    operator_name: str
    total_cases: int
    passed_cases: int
    failed_cases: int

    # 测试结果详情
    test_results: list[TestResult] = field(default_factory=list)

    # 汇总指标
    avg_abs_err: Optional[float] = None
    avg_rel_err: Optional[float] = None
    avg_cos_sim: Optional[float] = None

    # 报告路径
    report_path: str = "test/precision_report.md"

    # 是否满足要求
    meets_requirement: bool = False

    def calculate_summary(self) -> None:
        """计算汇总指标"""
        passed_results = [r for r in self.test_results if r.passed]

        if not passed_results:
            return

        abs_errors = [r.abs_err for r in passed_results if r.abs_err is not None]
        rel_errors = [r.rel_err for r in passed_results if r.rel_err is not None]
        cos_sims = [r.cos_sim for r in passed_results if r.cos_sim is not None]

        if abs_errors:
            self.avg_abs_err = sum(abs_errors) / len(abs_errors)
        if rel_errors:
            self.avg_rel_err = sum(rel_errors) / len(rel_errors)
        if cos_sims:
            self.avg_cos_sim = sum(cos_sims) / len(cos_sims)

        # 检查是否满足要求：至少30个测试用例，90%以上通过
        self.meets_requirement = (
            self.total_cases >= 30 and self.passed_cases / self.total_cases >= 0.9
        )

    def to_markdown(self) -> str:
        """转换为Markdown格式"""
        self.calculate_summary()

        lines = [
            f"# Precision Report: {self.operator_name}",
            "",
            f"**Total Cases**: {self.total_cases}",
            f"**Passed**: {self.passed_cases}",
            f"**Failed**: {self.failed_cases}",
            f"**Pass Rate**: {self.passed_cases / self.total_cases * 100:.1f}%",
            "",
        ]

        if self.avg_abs_err is not None:
            lines.append(f"**Avg Absolute Error**: {self.avg_abs_err:.6f}")
        if self.avg_rel_err is not None:
            lines.append(f"**Avg Relative Error**: {self.avg_rel_err:.6f}")
        if self.avg_cos_sim is not None:
            lines.append(f"**Avg Cosine Similarity**: {self.avg_cos_sim:.6f}")

        lines.append("")
        lines.append(f"**Meets Requirement**: {'Yes' if self.meets_requirement else 'No'}")
        lines.append("")

        # 详细结果表
        lines.append("## Test Results")
        lines.append("")
        lines.append("| # | Status | Abs Err | Rel Err | Cos Sim |")
        lines.append("|---|--------|---------|---------|---------|")

        for i, result in enumerate(self.test_results, 1):
            status = "PASS" if result.passed else "FAIL"
            abs_err = f"{result.abs_err:.6f}" if result.abs_err is not None else "-"
            rel_err = f"{result.rel_err:.6f}" if result.rel_err is not None else "-"
            cos_sim = f"{result.cos_sim:.6f}" if result.cos_sim is not None else "-"
            lines.append(f"| {i} | {status} | {abs_err} | {rel_err} | {cos_sim} |")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """递归序列化(含 test_results[*].test_case)。"""
        return {
            "operator_name": self.operator_name,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "test_results": [r.to_dict() for r in self.test_results],
            "avg_abs_err": self.avg_abs_err,
            "avg_rel_err": self.avg_rel_err,
            "avg_cos_sim": self.avg_cos_sim,
            "report_path": self.report_path,
            "meets_requirement": self.meets_requirement,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PrecisionReport":
        return cls(
            operator_name=data["operator_name"],
            total_cases=int(data["total_cases"]),
            passed_cases=int(data["passed_cases"]),
            failed_cases=int(data["failed_cases"]),
            test_results=[TestResult.from_dict(r) for r in data.get("test_results", [])],
            avg_abs_err=data.get("avg_abs_err"),
            avg_rel_err=data.get("avg_rel_err"),
            avg_cos_sim=data.get("avg_cos_sim"),
            report_path=data.get("report_path", "test/precision_report.md"),
            meets_requirement=bool(data.get("meets_requirement", False)),
        )


@dataclass
class PerformanceMetric:
    """单个性能指标"""

    case_name: str
    latency_ms: float  # 延迟(毫秒)
    throughput_gflops: float  # 吞吐(GFLOPS)
    memory_mb: float  # 内存占用(MB)


@dataclass
class PerformanceReport:
    """性能评估报告"""

    operator_name: str
    total_cases: int

    # 性能指标详情
    metrics: list[PerformanceMetric] = field(default_factory=list)

    # 汇总指标
    avg_latency_ms: Optional[float] = None
    avg_throughput_gflops: Optional[float] = None
    avg_memory_mb: Optional[float] = None

    # 报告路径
    report_path: str = "test/performance_report.md"

    # 是否满足要求
    meets_requirement: bool = False

    def calculate_summary(self) -> None:
        """计算汇总指标"""
        if not self.metrics:
            return

        latencies = [m.latency_ms for m in self.metrics]
        throughputs = [m.throughput_gflops for m in self.metrics]
        memories = [m.memory_mb for m in self.metrics]

        self.avg_latency_ms = sum(latencies) / len(latencies) if latencies else None
        self.avg_throughput_gflops = sum(throughputs) / len(throughputs) if throughputs else None
        self.avg_memory_mb = sum(memories) / len(memories) if memories else None

        # 检查是否满足要求：平均吞吐量 > 100 GFLOPS
        self.meets_requirement = (
            self.total_cases >= 10
            and self.avg_throughput_gflops is not None
            and self.avg_throughput_gflops > 100
        )

    def to_markdown(self) -> str:
        """转换为Markdown格式"""
        self.calculate_summary()

        lines = [
            f"# Performance Report: {self.operator_name}",
            "",
            f"**Total Cases**: {self.total_cases}",
            "",
        ]

        if self.avg_latency_ms is not None:
            lines.append(f"**Avg Latency**: {self.avg_latency_ms:.2f} ms")
        if self.avg_throughput_gflops is not None:
            lines.append(f"**Avg Throughput**: {self.avg_throughput_gflops:.2f} GFLOPS")
        if self.avg_memory_mb is not None:
            lines.append(f"**Avg Memory**: {self.avg_memory_mb:.2f} MB")

        lines.append("")
        lines.append(f"**Meets Requirement**: {'Yes' if self.meets_requirement else 'No'}")
        lines.append("")

        # 详细结果表
        lines.append("## Performance Metrics")
        lines.append("")
        lines.append("| # | Case | Latency (ms) | Throughput (GFLOPS) | Memory (MB) |")
        lines.append("|---|------|--------------|---------------------|-------------|")

        for i, metric in enumerate(self.metrics, 1):
            lines.append(
                f"| {i} | {metric.case_name} | {metric.latency_ms:.2f} | "
                f"{metric.throughput_gflops:.2f} | {metric.memory_mb:.2f} |"
            )

        return "\n".join(lines)


@dataclass
class PhaseResult:
    """阶段执行结果"""

    phase_name: str
    status: PhaseStatus
    data: Any = None
    message: str = ""
    errors: list[str] = field(default_factory=list)

    def is_success(self) -> bool:
        """是否成功"""
        return self.status == PhaseStatus.COMPLETED

    def requires_confirmation(self) -> bool:
        """是否需要用户确认"""
        return self.status == PhaseStatus.WAITING_CONFIRMATION

    def to_dict(self) -> dict:
        return {
            "phase_name": self.phase_name,
            "status": self.status.value,
            "data": self.data,
            "message": self.message,
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PhaseResult":
        return cls(
            phase_name=data["phase_name"],
            status=PhaseStatus(data["status"]),
            data=data.get("data"),
            message=data.get("message", ""),
            errors=list(data.get("errors", [])),
        )
