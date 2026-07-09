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

"""任务管理层 task_store 包(U1)。

runtime 任务层(不自建 domain flow)的数据层:独立 sqlite(sit CheckpointStore
之上,KTD1)+ R15 state rollup。一期-a 仅 tasks/task_threads/tasks_meta;
task_relations/task_artifacts_index 随 U5/U6-U8 consumer 落。
"""

from ascend_op_agent.task_store.artifacts import (
    ARTIFACT_TYPE_LOG,
    ARTIFACT_TYPE_REPORT,
    ARTIFACT_TYPE_SCRIPT,
    Artifact,
    ArtifactStore,
    MockArtifactWriter,
    make_writer,
)
from ascend_op_agent.task_store.models import (
    STATE_DONE,
    STATE_DRAFT,
    STATE_FAILED,
    STATE_NATIVE_NONE,
    STATE_NATIVE_PAUSED,
    STATE_PAUSED,
    STATE_RUNNING,
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
    TASK_TYPES,
    THREAD_DONE,
    THREAD_FAILED,
    THREAD_PENDING,
    THREAD_RUNNING,
    THREAD_WAITING_CONFIRM,
    Task,
)
from ascend_op_agent.task_store.rollup import rollup_task_state
from ascend_op_agent.task_store.store import TaskStore

__all__ = [
    "Task",
    "TaskStore",
    "rollup_task_state",
    "TASK_TYPES",
    "TASK_TYPE_MIGRATE",
    "TASK_TYPE_ANALYZE",
    "TASK_TYPE_OPTIMIZE",
    "TASK_TYPE_DEVELOP",
    "STATE_DRAFT",
    "STATE_RUNNING",
    "STATE_PAUSED",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_NATIVE_NONE",
    "STATE_NATIVE_PAUSED",
    "THREAD_PENDING",
    "THREAD_RUNNING",
    "THREAD_WAITING_CONFIRM",
    "THREAD_DONE",
    "THREAD_FAILED",
    "Artifact",
    "ArtifactStore",
    "MockArtifactWriter",
    "make_writer",
    "ARTIFACT_TYPE_REPORT",
    "ARTIFACT_TYPE_SCRIPT",
    "ARTIFACT_TYPE_LOG",
]
