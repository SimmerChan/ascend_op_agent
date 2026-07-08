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

"""U3 executor dispatch 单测。fake orchestrator(PhaseRunner.invoke)。"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_router import TaskExecutorUnavailable, TaskGatedError, TaskRouter
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
    TaskStore,
)


class FakeOrchestrator:
    """模拟 PhaseRunner:记录 invoke 调用,返回 state dict。"""

    def __init__(self):
        self.calls = []

    def invoke(self, user_input, thread_id):
        self.calls.append((user_input, thread_id))
        return {"current_phase": "codegen", "thread_id": thread_id, "ok": True}


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_dispatch_develop_calls_orchestrator_and_links_thread(store):
    orch = FakeOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(TASK_TYPE_DEVELOP, {"op": "add"})
    result = router.dispatch(tid, "开发 add 算子")
    assert orch.calls == [("开发 add 算子", result["thread_id"])]
    assert result["state"]["ok"] is True
    # thread 关联 task
    assert store.get_task_threads(tid) == [result["thread_id"]]


def test_dispatch_develop_uses_provided_thread_id(store):
    orch = FakeOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    result = router.dispatch(tid, "开发 add", thread_id="fixed-thread-1")
    assert result["thread_id"] == "fixed-thread-1"
    assert orch.calls[0][1] == "fixed-thread-1"
    assert store.get_task_threads(tid) == ["fixed-thread-1"]


def test_dispatch_develop_no_orchestrator_raises(store):
    router = TaskRouter(store, orchestrator=None)  # 未接线
    tid = store.create_task(TASK_TYPE_DEVELOP)
    with pytest.raises(TaskExecutorUnavailable, match="PhaseRunner"):
        router.dispatch(tid, "开发 add")


@pytest.mark.parametrize("gated_type", [TASK_TYPE_MIGRATE, TASK_TYPE_ANALYZE, TASK_TYPE_OPTIMIZE])
def test_dispatch_gated_types_raise(store, gated_type):
    orch = FakeOrchestrator()
    router = TaskRouter(store, orchestrator=orch)
    tid = store.create_task(gated_type)
    with pytest.raises(TaskGatedError, match="gated"):
        router.dispatch(tid, "x")


def test_dispatch_unknown_task_raises(store):
    router = TaskRouter(store, orchestrator=FakeOrchestrator())
    with pytest.raises(KeyError, match="unknown task"):
        router.dispatch("nope", "x")
