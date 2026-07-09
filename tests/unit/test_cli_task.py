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

"""U4 cli.py task 子命令组单测(CliRunner,--db/--ck 隔离 tmp,不污染 HOME)。"""

from __future__ import annotations

from click.testing import CliRunner

from ascend_op_agent.cli import main


def _invoke(db, ck, *args):
    runner = CliRunner()
    return runner.invoke(main, ["task", "--db", str(db), "--ck", str(ck), *args])


def test_task_list_empty(tmp_path):
    result = _invoke(tmp_path / "tasks.db", tmp_path / "ck.db", "list")
    assert result.exit_code == 0
    assert "无任务" in result.output


def test_task_new_and_list(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    r1 = _invoke(db, ck, "new", "develop")
    assert r1.exit_code == 0
    assert "created" in r1.output
    # list 看到 task
    r2 = _invoke(db, ck, "list")
    assert r2.exit_code == 0
    assert "develop" in r2.output
    assert "draft" in r2.output  # 无 thread → draft


def test_task_new_rejects_bad_type(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    result = _invoke(db, ck, "new", "bogus")
    assert result.exit_code == 1
    assert "unknown task type" in result.output


def test_task_select_and_progress(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    r_new = _invoke(db, ck, "new", "develop")
    # 抓 task_id(从 output 解析)
    tid = r_new.output.split("created ")[1].split(" ")[0]
    r_sel = _invoke(db, ck, "select", tid)
    assert r_sel.exit_code == 0
    assert "active task =" in r_sel.output
    r_prog = _invoke(db, ck, "progress")
    assert r_prog.exit_code == 0
    assert "state=" in r_prog.output
    assert tid in r_prog.output


def test_task_progress_no_active(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    _invoke(db, ck, "new", "develop")  # 建 + active
    # 清 active(用 commands 直清,模拟无 active)
    from ascend_op_agent.task_store import TaskStore

    TaskStore(db).clear_active()
    result = _invoke(db, ck, "progress")
    assert result.exit_code == 1
    assert "no active task" in result.output


def test_task_select_unknown(tmp_path):
    """testing P1:CLI task select unknown → exit 1(另两个错误出口已测,此分支补)。"""
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    result = _invoke(db, ck, "select", "nope")
    assert result.exit_code == 1
    assert "unknown task" in result.output


def test_store_link_thread_unknown_task_rejected(tmp_path):
    """adversarial P2:link_thread 防孤儿 task_threads 行(store 层校验)。"""
    from ascend_op_agent.task_store import TaskStore

    store = TaskStore(tmp_path / "tasks.db")
    import pytest

    with pytest.raises(KeyError, match="unknown task"):
        store.link_thread("phantom-task", "t1")


def test_store_set_active_unknown_task_rejected(tmp_path):
    """adversarial/reliability P3:set_active 防 phantom active。"""
    from ascend_op_agent.task_store import TaskStore

    store = TaskStore(tmp_path / "tasks.db")
    import pytest

    with pytest.raises(KeyError, match="unknown task"):
        store.set_active("phantom-task")


# ---- U5: link / unlink / edit-relation / suggest CLI ----


def _make_two_tasks(db, ck):
    """建两个 task,返回 (id_a, id_b)。"""
    from ascend_op_agent.task_store import TaskStore

    store = TaskStore(db)
    a = store.create_task("develop")
    b = store.create_task("analyze")
    return a, b


def test_task_link_and_unlink(tmp_path):
    """AE6:CLI task link <a> <b> depends-on → 落库;unlink 删除。"""
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, b = _make_two_tasks(db, ck)
    r_link = _invoke(db, ck, "link", a, b, "depends-on", "--confidence", "0.8")
    assert r_link.exit_code == 0
    assert "depends-on" in r_link.output
    assert a in r_link.output and b in r_link.output
    # 落库校验
    from ascend_op_agent.task_store import TaskStore
    from ascend_op_agent.task_store.relations import RelationStore

    rs = RelationStore(db)
    assert rs.get_relation(a, b, "depends-on") is not None
    # unlink
    r_unlink = _invoke(db, ck, "unlink", a, b)
    assert r_unlink.exit_code == 0
    assert "removed" in r_unlink.output
    assert rs.get_relation(a, b, "depends-on") is None


def test_task_link_unknown_task_errors(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, _ = _make_two_tasks(db, ck)
    r = _invoke(db, ck, "link", a, "phantom", "spawned-by")
    assert r.exit_code == 1
    assert "unknown task" in r.output


def test_task_link_bad_relation_type_errors(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, b = _make_two_tasks(db, ck)
    r = _invoke(db, ck, "link", a, b, "blocks")
    assert r.exit_code == 1
    assert "unknown relation type" in r.output


def test_task_edit_relation_in_place(tmp_path):
    """R2 edit:CLI edit-relation 改 type + confidence(不删+重插)。"""
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, b = _make_two_tasks(db, ck)
    _invoke(db, ck, "link", a, b, "depends-on", "--confidence", "0.3")
    r = _invoke(
        db, ck, "edit-relation", a, b, "--type", "spawned-by", "--confidence", "0.9"
    )
    assert r.exit_code == 0
    assert "spawned-by" in r.output
    # 旧 type 消失,新 type 在
    from ascend_op_agent.task_store.relations import RelationStore

    rs = RelationStore(db)
    assert rs.get_relation(a, b, "depends-on") is None
    rel = rs.get_relation(a, b, "spawned-by")
    assert rel is not None
    assert rel.confidence == 0.9


def test_task_edit_relation_not_found_errors(tmp_path):
    """Error:edit-relation 目标不存在 → exit 1。"""
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, b = _make_two_tasks(db, ck)
    r = _invoke(
        db, ck, "edit-relation", a, b, "--type", "spawned-by", "--confidence", "0.8"
    )
    assert r.exit_code == 1
    assert "no relation" in r.output


def test_task_suggest_no_active_errors(tmp_path):
    """suggest 无 active → NoActiveTaskError → exit 1。"""
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    _make_two_tasks(db, ck)  # 建 task 不设 active
    r = _invoke(db, ck, "suggest")
    assert r.exit_code == 1
    assert "no active task" in r.output


def test_task_unlink_no_match(tmp_path):
    db, ck = tmp_path / "tasks.db", tmp_path / "ck.db"
    a, b = _make_two_tasks(db, ck)
    r = _invoke(db, ck, "unlink", a, b)
    assert r.exit_code == 0
    assert "无关系" in r.output

