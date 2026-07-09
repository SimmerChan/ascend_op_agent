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

"""任务管理层 task_router 包(U3+):按 task.type 路由到执行器。

一期-a:develop → PhaseRunner 复用(insertion point:task 层接 non-op: 输入,
develop type 时 dispatch 转 op: 调用);migrate/analyze/optimize stub(gated on
Path A / 外部 executor,U8 接入)。一期-b 加 U7 意图分类器 + fixture 框架 + nudge。
"""

from ascend_op_agent.task_router.context_scope import ContextScope
from ascend_op_agent.task_router.executor_dispatch import (
    ExecutorNotImplemented,
    TaskExecutorUnavailable,
    TaskGatedError,
    TaskRouter,
)
from ascend_op_agent.task_router.fixture import (
    CalibrationReport,
    CalibrationRunner,
    FixtureError,
    FixtureLoader,
    LabeledExample,
    RuleBasedClassifier,
)
from ascend_op_agent.task_router.intent_classifier import (
    LABEL_OFF_TASK,
    LABEL_NEW_TASK,
    LABEL_ON_TASK,
    LABEL_PROGRESS_QUERY,
    LABELS,
    MODE_ALWAYS_ON_TASK,
    MODE_DEFAULT_ON,
    MODE_EXPLICIT_ONLY,
    MODES,
    ActiveTaskContext,
    ClassificationResult,
    IntentClassifier,
)
from ascend_op_agent.task_router.nudge import (
    NUDGE_FIRM,
    NUDGE_OFF,
    NUDGE_SOFT,
    NUDGE_MODES,
    NudgeResponder,
    NudgeResult,
)
from ascend_op_agent.task_router.relation_builder import (
    RelationBuilder,
    RelationSuggestion,
)

__all__ = [
    "TaskRouter",
    "TaskGatedError",
    "TaskExecutorUnavailable",
    "ExecutorNotImplemented",
    "RelationBuilder",
    "RelationSuggestion",
    "ContextScope",
    # U7 intent classifier + fixture + nudge
    "IntentClassifier",
    "ClassificationResult",
    "ActiveTaskContext",
    "LABEL_ON_TASK",
    "LABEL_OFF_TASK",
    "LABEL_NEW_TASK",
    "LABEL_PROGRESS_QUERY",
    "LABELS",
    "MODE_EXPLICIT_ONLY",
    "MODE_ALWAYS_ON_TASK",
    "MODE_DEFAULT_ON",
    "MODES",
    "FixtureLoader",
    "LabeledExample",
    "CalibrationRunner",
    "CalibrationReport",
    "FixtureError",
    "RuleBasedClassifier",
    "NudgeResponder",
    "NudgeResult",
    "NUDGE_OFF",
    "NUDGE_SOFT",
    "NUDGE_FIRM",
    "NUDGE_MODES",
]
