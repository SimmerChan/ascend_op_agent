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

"""U8 passthrough executor dispatch 集成测试(KTD8 + KTD10 + R10;AE4 / AE9 / AE10)。

锁 mock-vs-real gap:develop dispatch boundary 用 **真 PhaseRunner-shape orchestrator
+ 真 ArtifactStore + 真 make_writer**(U6),不 mock artifact writer。证明
produce(PhaseRunner)→ register(task_artifacts_index)→ read(ContextScope)跨 type
跨 task 通。
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_router import (
    ExecutorNotImplemented,
    TaskGatedError,
    TaskRouter,
)
from ascend_op_agent.task_router.context_scope import ContextScope
from ascend_op_agent.task_store import (
    TASK_TYPE_ANALYZE,
    TASK_TYPE_DEVELOP,
    TASK_TYPE_MIGRATE,
    TASK_TYPE_OPTIMIZE,
)
from ascend_op_agent.task_store.artifacts import ArtifactStore, WRITTEN_BY_EXECUTOR
from ascend_op_agent.task_store.relations import (
    RELATION_DEPENDS_ON,
    RelationStore,
)
from ascend_op_agent.task_store.store import TaskStore


class FakePhaseRunner:
    """PhaseRunner-shape orchestrator:返回带 ``artifacts`` 的 state。

    模拟"节点产产物后把 artifact 路径写进 state"(U8 boundary writer hook 提取契约)。
    只 mock 执行器本身,artifact 落库走 U6 真 ArtifactStore + 真 make_writer。
    """

    def __init__(self, artifacts):
        self._artifacts = artifacts
        self.calls = []

    def invoke(self, user_input, thread_id):
        self.calls.append((user_input, thread_id))
        return {
            "current_phase": "delivery",
            "thread_id": thread_id,
            "status": "done",
            "artifacts": self._artifacts,
        }


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "tasks.db"


@pytest.fixture
def task_store(db_path):
    return TaskStore(db_path)


@pytest.fixture
def relation_store(db_path):
    return RelationStore(db_path)


@pytest.fixture
def artifact_store(db_path):
    return ArtifactStore(db_path)


@pytest.fixture
def scope(task_store, relation_store, artifact_store):
    return ContextScope(task_store, relation_store, artifact_store)


# ---- AE4 e2e:produce → register → read 跨 type 跨 task(无 mock writer) ----


def test_ae4_develop_produces_artifact_depends_on_analyze_reads(scope, task_store,
                                                                 relation_store,
                                                                 artifact_store):
    """AE4 e2e(先处理 #12):develop 写 artifact → analyze depends-on 读到。

    全链路无 mock artifact writer:
      develop dispatch → PhaseRunner.invoke(真 shape)→ boundary make_writer(真)
      → task_artifacts_index(真 db)→ analyze 经 U6 ContextScope 读到。

    跨 type(develop→analyze)+ 跨 task,证明 produce→register→read 通。
    """
    # develop task:PhaseRunner 产出 report + script
    develop_id = task_store.create_task(TASK_TYPE_DEVELOP, {"op": "add"})
    runner = FakePhaseRunner(
        artifacts=[
            {"type": "report", "path": "/runs/dev-add/report.md"},
            {"type": "script", "path": "/runs/dev-add/adapt.py"},
        ]
    )
    router = TaskRouter(task_store, orchestrator=runner, artifact_store=artifact_store)

    result = router.dispatch(develop_id, "开发 add 算子")
    assert result["state"]["status"] == "done"

    # boundary writer 已落库(develop 标过产物)
    dev_arts = artifact_store.get_artifacts(develop_id)
    assert {(a.artifact_type, a.path) for a in dev_arts} == {
        ("report", "/runs/dev-add/report.md"),
        ("script", "/runs/dev-add/adapt.py"),
    }
    assert all(a.written_by == "phase_runner" for a in dev_arts)

    # analyze task depends-on develop → 经 U6 ContextScope 读 develop 的产物
    analyze_id = task_store.create_task(TASK_TYPE_ANALYZE)
    relation_store.add_relation(analyze_id, develop_id, RELATION_DEPENDS_ON)

    readable = scope.read_related_artifacts(analyze_id)
    readable_paths = {a.path for a in readable}
    assert readable_paths == {
        "/runs/dev-add/report.md",
        "/runs/dev-add/adapt.py",
    }
    # 权限域正确:全部指向 develop
    assert all(a.task_id == develop_id for a in readable)


def test_ae4_no_depends_on_edge_analyze_reads_nothing(scope, task_store, artifact_store):
    """AE4 edge:develop 写了 artifact,但 analyze 无 depends-on 边 → 读不到。

    证明读的是关系图授权,不是全局可见。
    """
    develop_id = task_store.create_task(TASK_TYPE_DEVELOP)
    runner = FakePhaseRunner(
        artifacts=[{"type": "report", "path": "/runs/x/r.md"}]
    )
    router = TaskRouter(task_store, orchestrator=runner, artifact_store=artifact_store)
    router.dispatch(develop_id, "x")

    analyze_id = task_store.create_task(TASK_TYPE_ANALYZE)
    # 不加 depends-on 边
    assert scope.read_related_artifacts(analyze_id) == []


def test_ae4_multiple_develop_sources_merged(scope, task_store, relation_store,
                                             artifact_store):
    """AE4:analyze depends-on 多个 develop task → 合并读全部产物。"""
    router_wired = []  # (task_id, runner)

    for op, path in [("add", "/add/r.md"), ("mul", "/mul/r.md")]:
        did = task_store.create_task(TASK_TYPE_DEVELOP, {"op": op})
        runner = FakePhaseRunner(artifacts=[{"type": "report", "path": path}])
        router_wired.append((did, runner))

    router = TaskRouter(task_store, orchestrator=None, artifact_store=artifact_store)
    # 每个 develop 单独 dispatch(换 orchestrator)
    for did, runner in router_wired:
        router.orchestrator = runner
        router.dispatch(did, "x")

    analyze_id = task_store.create_task(TASK_TYPE_ANALYZE)
    for did, _ in router_wired:
        relation_store.add_relation(analyze_id, did, RELATION_DEPENDS_ON)

    paths = {a.path for a in scope.read_related_artifacts(analyze_id)}
    assert paths == {"/add/r.md", "/mul/r.md"}


# ---- AE9 / AE10:passthrough ExecutorNotImplemented 边界 ----


def test_ae9_analyze_passthrough_raises_not_implemented(task_store):
    """AE9:analyze task dispatch → passthrough → ExecutorNotImplemented(follow-up 边界)。"""
    router = TaskRouter(task_store, orchestrator=None)
    tid = task_store.create_task(TASK_TYPE_ANALYZE)
    with pytest.raises(ExecutorNotImplemented, match="analyze"):
        router.dispatch(tid, "profile")


def test_ae10_optimize_passthrough_raises_not_implemented(task_store):
    """AE10:optimize task dispatch → passthrough → ExecutorNotImplemented(follow-up 边界)。"""
    router = TaskRouter(task_store, orchestrator=None)
    tid = task_store.create_task(TASK_TYPE_OPTIMIZE)
    with pytest.raises(ExecutorNotImplemented, match="optimize"):
        router.dispatch(tid, "tune")


# ---- KTD8 Path A gate ----


def test_migrate_gate_closed_then_open(task_store, tmp_path):
    """KTD8:spike 缺失 → TaskGatedError;建标志 → 不抛 gate 错(改抛 ExecutorNotImplemented)。"""
    spike = tmp_path / "spike"
    router = TaskRouter(task_store, orchestrator=None, path_a_spike_path=spike)
    tid = task_store.create_task(TASK_TYPE_MIGRATE)

    # gate closed
    with pytest.raises(TaskGatedError):
        router.dispatch(tid, "migrate")
    # gate open
    spike.write_text("ok")
    with pytest.raises(ExecutorNotImplemented, match="Path A"):
        router.dispatch(tid, "migrate")


# ---- 4-type routing 完整性(同一 router 实例) ----


def test_four_types_all_routable(task_store, artifact_store, tmp_path):
    """Integration:4 type 全可路由 —— develop 实跑写 artifact;migrate gate;
    analyze/optimize passthrough 边界。同一 db,跨 type 一致。"""
    spike = tmp_path / "no-spike"
    runner = FakePhaseRunner(artifacts=[{"type": "report", "path": "/d/r.md"}])
    router = TaskRouter(
        task_store, orchestrator=runner, artifact_store=artifact_store,
        path_a_spike_path=spike,
    )

    dev = task_store.create_task(TASK_TYPE_DEVELOP)
    res = router.dispatch(dev, "x")
    assert res["state"]["status"] == "done"
    assert len(artifact_store.get_artifacts(dev)) == 1

    mig = task_store.create_task(TASK_TYPE_MIGRATE)
    with pytest.raises(TaskGatedError):
        router.dispatch(mig, "x")

    anz = task_store.create_task(TASK_TYPE_ANALYZE)
    with pytest.raises(ExecutorNotImplemented):
        router.dispatch(anz, "x")

    opt = task_store.create_task(TASK_TYPE_OPTIMIZE)
    with pytest.raises(ExecutorNotImplemented):
        router.dispatch(opt, "x")
