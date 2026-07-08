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
