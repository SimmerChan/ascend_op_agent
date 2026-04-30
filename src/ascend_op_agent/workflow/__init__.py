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
"""

from ascend_op_agent.workflow.engine import OperatorWorkflow, WorkflowError, create_workflow
from ascend_op_agent.workflow.phases import (
    Phase,
    Phase0Init,
    Phase1Analysis,
    Phase2Design,
    Phase3CodeGen,
    Phase4Verify,
    Phase5Precision,
    PhaseResult,
    PhaseStatus,
)
from ascend_op_agent.workflow.models import (
    OpInfo,
    DesignDoc,
    CodeGenResult,
    CompileResult,
    PrecisionReport,
    MigrationStrategy,
    ArchitectureMapping,
)
from ascend_op_agent.workflow.skill_save import SkillSaver, OpResult, SkillDimension

__all__ = [
    "OperatorWorkflow",
    "create_workflow",
    "WorkflowError",
    "Phase",
    "Phase0Init",
    "Phase1Analysis",
    "Phase2Design",
    "Phase3CodeGen",
    "Phase4Verify",
    "Phase5Precision",
    "PhaseResult",
    "PhaseStatus",
    "OpInfo",
    "DesignDoc",
    "CodeGenResult",
    "CompileResult",
    "PrecisionReport",
    "MigrationStrategy",
    "ArchitectureMapping",
    "SkillSaver",
    "OpResult",
    "SkillDimension",
]
