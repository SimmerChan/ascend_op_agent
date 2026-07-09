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

"""U3 + U8 executor dispatch 单测。fake orchestrator(PhaseRunner.invoke)。

U8 变更:analyze/optimize 从一期-a 的 ``TaskGatedError`` 改为 passthrough
``ExecutorNotImplemented``(plan U8 / AE9 / AE10);migrate 仍 delivery-gated
(KTD8)。develop 路径加 dispatch boundary writer hook(#1 关键约束)。
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_router import (
    ExecutorNotImplemented,
    TaskExecutorUnavailable,
    TaskGatedError,
    TaskRouter,
)
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
    TaskStore,
)
from ascend_op_agent.task_store.artifacts import ArtifactStore


class FakeOrchestrator:
    """模拟 PhaseRunner:记录 invoke 调用,返回 state dict。

    产 artifact 的节点可在返回 state 里设 ``artifacts`` key(U8 boundary writer hook
    提取契约);默认不设(模拟一期-a 真 PhaseRunner,无 artifact 路径)。
    """

    def __init__(self, return_state=None):
        self.calls = []
        self._return_state = return_state

    def invoke(self, user_input, thread_id):
        self.calls.append((user_input, thread_id))
        if self._return_state is not None:
            return dict(self._return_state)
        return {"current_phase": "codegen", "thread_id": thread_id, "ok": True}


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


@pytest.fixture
def artifact_store(tmp_path):
    return ArtifactStore(tmp_path / "tasks.db")


# ---- develop:U3 既有路径(regression guard)+ U8 writer hook ----


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


def test_dispatch_develop_no_artifact_store_is_none_safe(store):
    """U8 #1:无 artifact_store → develop 路径不写 artifact 也不崩(None-safe)。

    证明 U8 writer hook 不让一期-a develop 路径 regress。
    """
    orch = FakeOrchestrator(
        return_state={"thread_id": "t", "artifacts": [{"type": "report", "path": "/r.md"}]}
    )
    router = TaskRouter(store, orchestrator=orch, artifact_store=None)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    result = router.dispatch(tid, "x")  # 不崩
    assert result["state"]["artifacts"][0]["path"] == "/r.md"


def test_dispatch_develop_writer_hook_registers_artifact_dict_form(
    store, artifact_store
):
    """U8 #1:develop dispatch boundary 经 U6 真 make_writer 把 PhaseRunner 产出
    的 artifact(dict 形态)注册到 task_artifacts_index。无 mock writer。"""
    orch = FakeOrchestrator(
        return_state={
            "thread_id": "t",
            "artifacts": [
                {"type": "report", "path": "/runs/dev/r.md"},
                {"type": "script", "path": "/runs/dev/s.py"},
            ],
        }
    )
    router = TaskRouter(store, orchestrator=orch, artifact_store=artifact_store)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    router.dispatch(tid, "x")

    arts = artifact_store.get_artifacts(tid)
    paths = {(a.artifact_type, a.path) for a in arts}
    assert paths == {
        ("report", "/runs/dev/r.md"),
        ("script", "/runs/dev/s.py"),
    }
    # written_by 标 phase_runner(dispatch boundary writer)
    assert all(a.written_by == "phase_runner" for a in arts)


def test_dispatch_develop_writer_hook_registers_artifact_pair_form(
    store, artifact_store
):
    """提取契约宽容:(type, path) 序列对形态也认。"""
    orch = FakeOrchestrator(
        return_state={"thread_id": "t", "artifacts": [("log", "/l.log")]}
    )
    router = TaskRouter(store, orchestrator=orch, artifact_store=artifact_store)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    router.dispatch(tid, "x")
    arts = artifact_store.get_artifacts(tid)
    assert len(arts) == 1
    assert arts[0].artifact_type == "log"
    assert arts[0].path == "/l.log"


def test_dispatch_develop_writer_hook_no_artifact_in_state_no_write(
    store, artifact_store
):
    """一期-a 真 PhaseRunner state 无 artifacts key → 不写(None-safe,不崩)。"""
    orch = FakeOrchestrator()  # 默认 state 无 artifacts
    router = TaskRouter(store, orchestrator=orch, artifact_store=artifact_store)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    router.dispatch(tid, "x")
    assert artifact_store.get_artifacts(tid) == []


def test_dispatch_develop_writer_hook_upsert_idempotent(store, artifact_store):
    """同 (task_id, path) 二次 dispatch 幂等 upsert(U6 ArtifactStore.write PK 行为)。"""
    orch = FakeOrchestrator(
        return_state={"thread_id": "t", "artifacts": [{"type": "report", "path": "/r.md"}]}
    )
    router = TaskRouter(store, orchestrator=orch, artifact_store=artifact_store)
    tid = store.create_task(TASK_TYPE_DEVELOP)
    router.dispatch(tid, "x")
    router.dispatch(tid, "x again")  # 再跑一次,同 path
    arts = artifact_store.get_artifacts(tid)
    assert len(arts) == 1  # 不重复


# ---- migrate:Path A delivery gate(KTD8) ----


def test_dispatch_migrate_gate_closed_raises_task_gated(store, tmp_path):
    """KTD8 happy(edge):spike 标志缺失 → migrate raise TaskGatedError + state 不变。"""
    spike = tmp_path / "no-spike"  # 不存在
    router = TaskRouter(
        store, orchestrator=FakeOrchestrator(), path_a_spike_path=spike
    )
    tid = store.create_task(TASK_TYPE_MIGRATE)
    state_native_before = store.get_task(tid).state_native
    with pytest.raises(TaskGatedError, match="Path A"):
        router.dispatch(tid, "migrate model")
    # task state_native 不变(still "" / draft 由"无 thread"推导);Task.state 是
    # rollup 推导非存储字段,断言存储的 state_native 才是 source of truth
    assert store.get_task(tid).state_native == state_native_before
    # 未 link 任何 thread(gate 在 dispatch 前拦截,thread 集合仍空)
    assert store.get_task_threads(tid) == []


def test_dispatch_migrate_gate_open_does_not_raise_gate(store, tmp_path):
    """KTD8 happy:建 spike 标志 → migrate 不抛 gate 错。

    本 unit 选择:gate 过后 raise ExecutorNotImplemented(Path A executor 未接)。
    关键断言是"不抛 TaskGatedError"。
    """
    spike = tmp_path / "path_a.spike_passed"
    spike.write_text("spike-ok")
    router = TaskRouter(
        store, orchestrator=FakeOrchestrator(), path_a_spike_path=spike
    )
    tid = store.create_task(TASK_TYPE_MIGRATE)
    with pytest.raises(ExecutorNotImplemented, match="Path A"):
        router.dispatch(tid, "migrate model")


def test_dispatch_migrate_gate_default_path_is_user_home():
    """默认 gate 路径 = ~/.ascend_op_agent/path_a.spike_passed(不污染 home,仅验常量)。"""
    from pathlib import Path

    from ascend_op_agent.task_router.executor_dispatch import DEFAULT_PATH_A_SPIKE_PATH

    assert DEFAULT_PATH_A_SPIKE_PATH == Path(
        "~/.ascend_op_agent/path_a.spike_passed"
    ).expanduser()


# ---- analyze / optimize:passthrough ExecutorNotImplemented(AE9 / AE10) ----


def test_dispatch_analyze_raises_executor_not_implemented(store):
    """AE9:analyze → passthrough → ExecutorNotImplemented(明确 follow-up 边界)。"""
    router = TaskRouter(store, orchestrator=FakeOrchestrator())
    tid = store.create_task(TASK_TYPE_ANALYZE)
    with pytest.raises(ExecutorNotImplemented, match="analyze"):
        router.dispatch(tid, "profile model")


def test_dispatch_optimize_raises_executor_not_implemented(store):
    """AE10:optimize → passthrough → ExecutorNotImplemented(明确 follow-up 边界)。"""
    router = TaskRouter(store, orchestrator=FakeOrchestrator())
    tid = store.create_task(TASK_TYPE_OPTIMIZE)
    with pytest.raises(ExecutorNotImplemented, match="optimize"):
        router.dispatch(tid, "tune op")


# ---- routing 完整性 ----


def test_dispatch_unknown_task_raises(store):
    router = TaskRouter(store, orchestrator=FakeOrchestrator())
    with pytest.raises(KeyError, match="unknown task"):
        router.dispatch("nope", "x")


def test_dispatch_all_four_types_routable(store, tmp_path):
    """Integration:4 type 全可路由(develop 实跑;analyze/optimize/migrate gate 边界)。

    每种 type 都能进入 dispatch 不因"unknown type"失败 —— routing 完整。
    """
    spike = tmp_path / "no-spike"
    router = TaskRouter(
        store, orchestrator=FakeOrchestrator(), path_a_spike_path=spike
    )
    # develop 实跑
    dev = store.create_task(TASK_TYPE_DEVELOP)
    assert router.dispatch(dev, "x")["state"]["ok"] is True
    # 其余三种都抛错(但能路由到对应 handler,不是"unknown")
    mig = store.create_task(TASK_TYPE_MIGRATE)
    with pytest.raises(TaskGatedError):
        router.dispatch(mig, "x")
    anz = store.create_task(TASK_TYPE_ANALYZE)
    with pytest.raises(ExecutorNotImplemented):
        router.dispatch(anz, "x")
    opt = store.create_task(TASK_TYPE_OPTIMIZE)
    with pytest.raises(ExecutorNotImplemented):
        router.dispatch(opt, "x")
