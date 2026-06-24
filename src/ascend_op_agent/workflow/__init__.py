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

"""Workflow 数据契约与执行封装。

U8 死代码清理后保留 3 个职责:

- ``models.py`` — 核心数据契约(OpInfo/DesignDoc/CodeGenResult/CompileResult/
  PrecisionReport/PhaseResult 等 dataclass + to_dict/from_dict serde)。F2 落地。
- ``compiler.py`` — cann_compile subprocess 封装(确定性编译)。
- ``performance.py`` — torch_npu.profiler 性能采集。

旧的 ``engine.py`` / ``phases.py`` / ``adapters.py`` / ``skill_save.py`` 是
**从未被 backend.py / agent.core.py / CLI 调用过的死代码**(3759 LOC),
已被 ``ascend_op_agent.orchestrator`` (LangGraph-free 自研状态机)替代。

迁移指南:旧 ``OperatorWorkflow`` / ``Phase0..5Init`` / ``SkillSaver`` /
``PyTorchAdapter`` 等的等价能力由 orchestrator + cannbot-skills 提供,
参考 ``docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md``。
"""

# 核心数据契约(必加载)
from ascend_op_agent.workflow.models import (
    ArchitectureMapping,
    CodeGenResult,
    CompileResult,
    DesignDoc,
    FileChange,
    MigrationStrategy,
    OpInfo,
    PhaseResult,
    PhaseStatus,
    PrecisionReport,
    TestCase,
    TestResult,
)

# 执行封装(可选加载,触发 import 错误时降级为 None,避免阻塞 models)
try:
    from ascend_op_agent.workflow.compiler import (
        compile_operator,
        fix_compile_errors,
    )
except Exception:
    compile_operator = None  # type: ignore[assignment]
    fix_compile_errors = None  # type: ignore[assignment]

try:
    from ascend_op_agent.workflow.performance import (
        PerformanceMetric,
        BenchmarkCase,
        PerformanceResult,
        PerformanceReport,
        PerformanceEvaluator,
    )
except Exception:
    PerformanceMetric = None  # type: ignore[assignment]
    BenchmarkCase = None  # type: ignore[assignment]
    PerformanceResult = None  # type: ignore[assignment]
    PerformanceReport = None  # type: ignore[assignment]
    PerformanceEvaluator = None  # type: ignore[assignment]

__all__ = [
    # models(U5 serde 落地,F2 修正)
    "OpInfo",
    "DesignDoc",
    "CodeGenResult",
    "CompileResult",
    "PrecisionReport",
    "MigrationStrategy",
    "ArchitectureMapping",
    "PhaseResult",
    "PhaseStatus",
    "FileChange",
    "TestCase",
    "TestResult",
    # compiler(可能在 degraded 环境下为 None)
    "compile_operator",
    "fix_compile_errors",
    # performance(可能在 degraded 环境下为 None)
    "PerformanceMetric",
    "BenchmarkCase",
    "PerformanceResult",
    "PerformanceReport",
    "PerformanceEvaluator",
]
