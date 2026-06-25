"""SSHEnvironment._wrap_command exit code 传播测试(e2e 2026-06-25 暴露的真 bug)。

Bug 复现: wrapper 用 \\n 拼接多行命令,SSH channel 读到的 exit 是最后一行
(printf CWD marker = 0)。eval 失败时真实 exit code 被吞,NpuExecutor 报 success。
"""

from __future__ import annotations

from ascend_op_agent.ssh.manager import SSHEnvironment


def test_wrap_command_preserves_eval_exit_code() -> None:
    """wrapper 最后必须 `exit $_hermes_eval_exit`,否则 printf 0 会冲掉真实失败。"""
    ssh = SSHEnvironment(host="x", user="x", port=22, timeout=10)
    wrapped = ssh._wrap_command("some_user_cmd", "/tmp")
    # 关键断言:wrapper 末尾有 `exit $_hermes_eval_exit`
    assert "_hermes_eval_exit=$?" in wrapped
    assert "exit $_hermes_eval_exit" in wrapped
    # 顺序: eval → 捕获 → export(可选)→ printf → exit
    eval_idx = wrapped.index("eval 'some_user_cmd'")
    capture_idx = wrapped.index("_hermes_eval_exit=$?")
    assert eval_idx < capture_idx, "捕获必须在 eval 之后"


def test_wrap_command_cd_failure_still_exits_126() -> None:
    """cd 失败时 wrapper 用 || exit 126 早退,不影响 eval exit 捕获。"""
    ssh = SSHEnvironment(host="x", user="x", port=22, timeout=10)
    wrapped = ssh._wrap_command("x", "/tmp")
    assert "builtin cd" in wrapped
    assert "|| exit 126" in wrapped


def test_wrap_command_preserves_single_quotes_in_user_command() -> None:
    """用户命令里的单引号必须正确转义,不被 wrapper 吞错。"""
    ssh = SSHEnvironment(host="x", user="x", port=22, timeout=10)
    user_cmd = "docker exec ops_pt bash -c 'source /etc/profile && echo hi'"
    wrapped = ssh._wrap_command(user_cmd, "/tmp")
    # eval 行的内容必须包含转义后的用户命令
    assert "eval '" in wrapped
    # 'X' → '\\''X' 形式
    assert "exec ops_pt" in wrapped


def test_wrap_command_no_snapshot_skips_export() -> None:
    """snapshot 未就绪时不写 export -p 那一行(避免错误地写文件)。"""
    ssh = SSHEnvironment(host="x", user="x", port=22, timeout=10)
    ssh._snapshot_ready = False
    wrapped = ssh._wrap_command("x", "/tmp")
    assert "source" not in wrapped
    assert "export -p" not in wrapped
    # 但 eval/capture/exit 仍必须有
    assert "eval 'x'" in wrapped
    assert "_hermes_eval_exit=$?" in wrapped
    assert "exit $_hermes_eval_exit" in wrapped
