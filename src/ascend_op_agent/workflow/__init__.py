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

"""Workflow工作流模块

实现六阶段算子开发工作流:
- Phase0: 初始化（环境检测）
- Phase1: 需求分析（自动）
- Phase2: 方案设计（用户确认）
- Phase3: 代码生成
- Phase4: 编译验证
- Phase5: 精度评估（≥30用例，必选）

注:engine/phases/skill_save/performance 是 P0 重构中的死代码层(U8 计划删除),
其环境依赖(chromadb/sklearn/pandas/numpy)在不兼容环境下会触发 import 错误。
为避免阻塞 models(核心数据契约)及其下游(orchestrator),用 try/except 降级。
"""

# 核心数据契约:必加载,失败立即抛
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

# 死代码层:U8 删除。try/except 防环境依赖阻塞 models。
try:
    from ascend_op_agent.workflow.engine import (
        OperatorWorkflow,
        WorkflowError,
        create_workflow,
    )
except Exception:
    OperatorWorkflow = None  # type: ignore[assignment]
    WorkflowError = None  # type: ignore[assignment]
    create_workflow = None  # type: ignore[assignment]

try:
    from ascend_op_agent.workflow.phases import (
        Phase,
        Phase0Init,
        Phase1Analysis,
        Phase2Design,
        Phase3CodeGen,
        Phase4Verify,
        Phase5Precision,
    )
except Exception:
    Phase = None  # type: ignore[assignment]
    Phase0Init = None  # type: ignore[assignment]
    Phase1Analysis = None  # type: ignore[assignment]
    Phase2Design = None  # type: ignore[assignment]
    Phase3CodeGen = None  # type: ignore[assignment]
    Phase4Verify = None  # type: ignore[assignment]
    Phase5Precision = None  # type: ignore[assignment]

try:
    from ascend_op_agent.workflow.skill_save import (
        SkillSaver,
        OpResult,
        SkillDimension,
    )
except Exception:
    SkillSaver = None  # type: ignore[assignment]
    OpResult = None  # type: ignore[assignment]
    SkillDimension = None  # type: ignore[assignment]

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
    # 核心数据契约(models)
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
    # 死代码(可能在 degraded 环境下为 None)
    "OperatorWorkflow",
    "WorkflowError",
    "create_workflow",
    "Phase",
    "Phase0Init",
    "Phase1Analysis",
    "Phase2Design",
    "Phase3CodeGen",
    "Phase4Verify",
    "Phase5Precision",
    "SkillSaver",
    "OpResult",
    "SkillDimension",
    "PerformanceMetric",
    "BenchmarkCase",
    "PerformanceResult",
    "PerformanceReport",
    "PerformanceEvaluator",
]
