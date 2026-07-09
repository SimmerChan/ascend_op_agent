# Copyright 2026 SimperChan
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

"""U6 ArtifactStore 单测(KTD10 artifact registry)。

覆盖 KTD10 schema 行为 + writer hook spec + mock writer:
- Happy:write / get_artifacts / get_artifacts_by_type
- Edge:同 (task_id, path) 幂等 upsert(更新 type/by/at);空 task → []
- Error:artifact 不存在 → 读返回空(不崩)
- writer hook:make_writer 落库;MockArtifactWriter 记录调用 + 可选 forward
- Integration:跨实例持久化(同 db path)
"""

from __future__ import annotations

import pytest

from ascend_op_agent.task_store.artifacts import (
    ARTIFACT_TYPE_LOG,
    ARTIFACT_TYPE_REPORT,
    ARTIFACT_TYPE_SCRIPT,
    WRITTEN_BY_EXECUTOR,
    WRITTEN_BY_MANUAL,
    ArtifactStore,
    MockArtifactWriter,
    make_writer,
)


@pytest.fixture
def store(tmp_path):
    return ArtifactStore(tmp_path / "tasks.db")


def test_write_and_get_artifacts(store):
    art = store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/report.md", "executor")
    assert art.task_id == "t1"
    assert art.artifact_type == ARTIFACT_TYPE_REPORT
    assert art.path == "/tmp/report.md"
    assert art.written_by == "executor"
    assert art.written_at != ""
    arts = store.get_artifacts("t1")
    assert len(arts) == 1
    assert arts[0].path == "/tmp/report.md"


def test_write_default_written_by_manual(store):
    art = store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/r.md")
    assert art.written_by == WRITTEN_BY_MANUAL


def test_write_idempotent_upsert_same_task_path(store):
    """Edge:同 (task_id, path) 再写 → upsert(PK 冲突),更新 type/by/at。

    同 task 同 path 不会产生重复行。
    """
    a1 = store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/r.md", "executor")
    a2 = store.write("t1", ARTIFACT_TYPE_LOG, "/tmp/r.md", "manual")
    assert a2.artifact_type == ARTIFACT_TYPE_LOG  # 更新
    assert a2.written_by == "manual"
    arts = store.get_artifacts("t1")
    assert len(arts) == 1  # 不重复
    assert arts[0].artifact_type == ARTIFACT_TYPE_LOG


def test_multiple_artifacts_distinct_paths(store):
    store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/a.md")
    store.write("t1", ARTIFACT_TYPE_SCRIPT, "/tmp/b.py")
    store.write("t1", ARTIFACT_TYPE_LOG, "/tmp/c.log")
    arts = store.get_artifacts("t1")
    assert len(arts) == 3
    paths = {a.path for a in arts}
    assert paths == {"/tmp/a.md", "/tmp/b.py", "/tmp/c.log"}


def test_get_artifacts_empty_no_task(store):
    """Error:artifact 不存在 → 读返回空(不崩)。"""
    assert store.get_artifacts("nonexistent") == []


def test_get_artifacts_by_type(store):
    store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/r1.md")
    store.write("t1", ARTIFACT_TYPE_SCRIPT, "/tmp/s1.py")
    store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/r2.md")
    reports = store.get_artifacts_by_type("t1", ARTIFACT_TYPE_REPORT)
    assert len(reports) == 2
    assert {a.path for a in reports} == {"/tmp/r1.md", "/tmp/r2.md"}
    scripts = store.get_artifacts_by_type("t1", ARTIFACT_TYPE_SCRIPT)
    assert len(scripts) == 1


def test_get_artifacts_isolated_per_task(store):
    store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/a.md")
    store.write("t2", ARTIFACT_TYPE_REPORT, "/tmp/b.md")
    assert {a.path for a in store.get_artifacts("t1")} == {"/tmp/a.md"}
    assert {a.path for a in store.get_artifacts("t2")} == {"/tmp/b.md"}


def test_persistence_across_instances(store, tmp_path):
    """Integration:reopen ArtifactStore 同 db path,产物仍可读。"""
    store.write("t1", ARTIFACT_TYPE_REPORT, "/tmp/r.md", "executor")
    store2 = ArtifactStore(tmp_path / "tasks.db")
    arts = store2.get_artifacts("t1")
    assert len(arts) == 1
    assert arts[0].path == "/tmp/r.md"


def test_make_writer_writes_to_store(store):
    """writer hook spec:make_writer 落库到绑定的 store。"""
    writer = make_writer(store, written_by=WRITTEN_BY_EXECUTOR)
    writer("t1", ARTIFACT_TYPE_REPORT, "/tmp/via_writer.md")
    arts = store.get_artifacts("t1")
    assert len(arts) == 1
    assert arts[0].path == "/tmp/via_writer.md"
    assert arts[0].written_by == WRITTEN_BY_EXECUTOR


def test_mock_writer_records_calls_no_forward():
    """MockArtifactWriter 不 forward → 记录调用,store 空。"""
    mock = MockArtifactWriter()
    mock("t1", ARTIFACT_TYPE_REPORT, "/tmp/mock.md")
    assert len(mock.calls) == 1
    assert mock.calls[0].task_id == "t1"
    assert mock.calls[0].path == "/tmp/mock.md"


def test_mock_writer_forward_to_store(store):
    """MockArtifactWriter forward_store → 落库 + 记录(mock 不影响跨 task 读逻辑)。"""
    mock = MockArtifactWriter(forward_store=store)
    mock("t1", ARTIFACT_TYPE_REPORT, "/tmp/fwd.md")
    assert len(mock.calls) == 1
    arts = store.get_artifacts("t1")
    assert len(arts) == 1
    assert arts[0].path == "/tmp/fwd.md"
