"""U14: 闭环修复控制器 + 上下文压缩单测。

不依赖 LLM API key 也不依赖硬件 —— review/fix 节点是 mock callable。

覆盖:

- ``run_fix_loop``:clean 终止 / fatal 终止 / max_rounds 终止 / fatal keyword 升级
- ``make_fix_loop_node``:包成 PhaseRunner Node 的形态
- ``compress_transcript``:短列表透传 / 长列表压缩 / 工具结果丢弃 / 摘要截断
"""

from __future__ import annotations

import pytest

from ascend_op_agent.orchestrator import (
    Node,
    ReviewResult,
    compress_transcript,
    make_fix_loop_node,
    run_fix_loop,
)


# ---- run_fix_loop ----


def test_run_fix_loop_clean_in_one_round() -> None:
    """review clean → 立即 status=done,不调 fix。"""
    fix_calls = {"n": 0}

    def review(_state):
        return ReviewResult(clean=True, raw_response="LGTM")

    def fix(_state, _issues):
        fix_calls["n"] += 1
        return {}

    result = run_fix_loop({}, "compile", review, fix, max_rounds=5)
    assert result["status"] == "done"
    assert result["clean"] is True
    assert result["rounds"] == 1
    assert result["reason"] == "clean"
    assert fix_calls["n"] == 0  # clean 不需要 fix


def test_run_fix_loop_converges_in_three_rounds() -> None:
    """前 2 轮有问题,第 3 轮 clean → status=done。"""
    rounds_seen = {"n": 0}

    def review(_state):
        rounds_seen["n"] += 1
        if rounds_seen["n"] < 3:
            return ReviewResult(clean=False, issues=["bug"])
        return ReviewResult(clean=True)

    def fix(state, issues):
        state.setdefault("fix_count", 0)
        state["fix_count"] += 1
        return {"fix_count": state["fix_count"]}

    result = run_fix_loop({}, "compile", review, fix, max_rounds=5)
    assert result["status"] == "done"
    assert result["rounds"] == 3
    assert result["clean"] is True


def test_run_fix_loop_max_rounds_terminates_failed() -> None:
    """总是有问题 → max_rounds 终止 status=failed。"""
    def review(_state):
        return ReviewResult(clean=False, issues=["persistent bug"])

    def fix(state, _issues):
        return {"attempt": state.get("attempt", 0) + 1}

    result = run_fix_loop({}, "compile", review, fix, max_rounds=3)
    assert result["status"] == "failed"
    assert result["reason"] == "max_rounds"
    assert result["rounds"] == 3
    assert result["clean"] is False


def test_run_fix_loop_fatal_signal_terminates_failed() -> None:
    """review.fatal=True → 立即 status=failed reason=fatal。"""
    def review(_state):
        return ReviewResult(clean=False, fatal=True, issues=["unsupported dtype fp128"])

    def fix(_state, _issues):
        return {}

    result = run_fix_loop({}, "precision", review, fix, max_rounds=5)
    assert result["status"] == "failed"
    assert result["reason"] == "fatal"
    assert result["rounds"] == 1


def test_run_fix_loop_fatal_keyword_upgrades_to_fatal() -> None:
    """review.raw_response 含 fatal keyword → 升级为 fatal。"""
    def review(_state):
        # clean=False, fatal=False,但响应里含 "unsupported dtype"
        return ReviewResult(
            clean=False,
            fatal=False,
            issues=["bug"],
            raw_response="Error: unsupported dtype on this kernel",
        )

    def fix(_state, _issues):
        return {}

    result = run_fix_loop(
        {},
        "compile",
        review,
        fix,
        max_rounds=5,
        fatal_keywords=("unsupported dtype",),
    )
    assert result["status"] == "failed"
    assert result["reason"] == "fatal"
    assert result["rounds"] == 1


def test_run_fix_loop_custom_fatal_keywords_override_defaults() -> None:
    """自定义 fatal_keywords 替换默认列表(不命中默认 'unsupported dtype' 时)。"""
    def review(_state):
        return ReviewResult(
            clean=False,
            raw_response="Error: hardcoded feature XYZ not supported",
        )

    def fix(_state, _issues):
        return {}

    # 默认 keyword 不命中 → 应该跑满 max_rounds
    result_default = run_fix_loop({}, "compile", review, fix, max_rounds=2)
    assert result_default["reason"] == "max_rounds"

    # 自定义 keyword 命中 → fatal
    result_custom = run_fix_loop(
        {},
        "compile",
        review,
        fix,
        max_rounds=5,
        fatal_keywords=("hardcoded feature",),
    )
    assert result_custom["reason"] == "fatal"


def test_run_fix_loop_fix_update_applied_to_state() -> None:
    """fix 返回的 update 写回 state,下一轮 review 看到修复后的产物。"""
    seen_counter: list[int] = []

    def review(state):
        seen_counter.append(state.get("counter", 0))
        if state.get("counter", 0) >= 2:
            return ReviewResult(clean=True)
        return ReviewResult(clean=False, issues=["counter too low"])

    def fix(state, _issues):
        return {"counter": state.get("counter", 0) + 1}

    result = run_fix_loop({}, "compile", review, fix, max_rounds=5)
    assert result["status"] == "done"
    # review 看到 [0(初始), 1(fix后), 2(fix后)] → 第 3 轮 clean
    assert seen_counter == [0, 1, 2]


def test_run_fix_loop_rejects_invalid_max_rounds() -> None:
    """max_rounds < 1 抛 ValueError。"""
    with pytest.raises(ValueError, match="max_rounds"):
        run_fix_loop({}, "compile", lambda s: ReviewResult(clean=True), lambda s, i: {}, max_rounds=0)


# ---- make_fix_loop_node ----


def test_make_fix_loop_node_returns_node_with_named_result() -> None:
    """make_fix_loop_node 包成 Node,result 写入 state['<phase>_result']。"""
    review_node = Node(
        name="review",
        func=lambda s: {"clean": True, "raw_response": "LGTM"},
    )
    fix_node = Node(name="fix", func=lambda s: {})

    loop_node = make_fix_loop_node(
        phase="compile_fix_loop",
        kind="compile",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=3,
    )

    assert loop_node.name == "compile_fix_loop"
    update = loop_node.func({})
    assert "compile_fix_loop_result" in update
    assert update["compile_fix_loop_result"]["status"] == "done"
    assert update["compile_fix_loop_result"]["rounds"] == 1


def test_make_fix_loop_node_handles_failed_loop() -> None:
    """make_fix_loop_node 包成的节点,fail 时 result.status=failed。"""
    review_node = Node(
        name="review",
        func=lambda s: {"clean": False, "issues": ["bug"], "raw_response": "still broken"},
    )
    fix_node = Node(name="fix", func=lambda s: {"fix_attempted": True})

    loop_node = make_fix_loop_node(
        phase="precision_fix_loop",
        kind="precision",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=2,
    )
    update = loop_node.func({})
    assert update["precision_fix_loop_result"]["status"] == "failed"
    assert update["precision_fix_loop_result"]["rounds"] == 2


def test_make_fix_loop_node_cleans_up_temp_issues_field() -> None:
    """loop node 完成后,_fix_loop_issues 临时字段应被清除。"""
    review_node = Node(name="review", func=lambda s: {"clean": False, "issues": ["x"]})
    fix_node = Node(name="fix", func=lambda s: {})
    loop_node = make_fix_loop_node(
        phase="compile_fix_loop",
        kind="compile",
        review_node=review_node,
        fix_node=fix_node,
        max_rounds=1,
    )
    state = {}
    loop_node.func(state)
    assert "_fix_loop_issues" not in state


# ---- compress_transcript ----


def test_compress_transcript_short_passthrough() -> None:
    """messages 短于 keep_last_n + 1 → 原样返回。"""
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    out = compress_transcript(msgs, keep_last_n=4)
    assert out == msgs


def test_compress_transcript_empty() -> None:
    assert compress_transcript([], keep_last_n=4) == []


def test_compress_transcript_compresses_middle() -> None:
    """10 条消息,keep_last_n=3:首条 + 6 条中间(摘要)+ 3 条尾部。"""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(1, 10):
        msgs.append({"role": "user" if i % 2 else "assistant", "content": f"msg-{i}"})

    out = compress_transcript(msgs, keep_last_n=3)

    # 首 + 6 中间(去尾 3 后剩 1..6) + 3 尾(7,8,9) = 10 条
    # 实际:keep_last_n+1 = 4,总长 10 > 4,触发压缩
    # 首条 + (10-1-3=6 条中间) + 3 条尾 = 10
    assert len(out) == 10  # 工具结果未触发丢,条数不变,但中间内容被截
    # 首条原样
    assert out[0] == {"role": "system", "content": "sys"}
    # 尾部 3 条原样
    assert out[-1] == msgs[-1]
    assert out[-2] == msgs[-2]
    assert out[-3] == msgs[-3]


def test_compress_transcript_truncates_long_middle_content() -> None:
    """中间消息 content > 200 字 → 截断 + ... [compressed] 后缀。"""
    long_content = "x" * 500
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": long_content},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "r2"},
    ]
    out = compress_transcript(msgs, keep_last_n=2)
    # 第 1 条(msgs[1])是中间,被截断
    middle = out[1]
    assert "[compressed]" in middle["content"]
    assert len(middle["content"]) < len(long_content)


def test_compress_transcript_drops_tool_messages_in_middle() -> None:
    """中间消息 role=tool → 整条丢弃。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "tool", "content": "huge tool output that should be dropped"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    out = compress_transcript(msgs, keep_last_n=2)
    # 中间是 msgs[1..3] (user, tool, assistant),tool 被丢
    roles = [m["role"] for m in out]
    assert "tool" not in roles
    # system 保留 + 中间 user/assistant + 尾部 user/assistant
    assert "system" in roles


def test_compress_transcript_keeps_short_content_unchanged_in_middle() -> None:
    """中间消息 content <= 200 字 → 不截断,原样保留。"""
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "short middle msg"},
        {"role": "assistant", "content": "tail1"},
        {"role": "user", "content": "tail2"},
    ]
    out = compress_transcript(msgs, keep_last_n=2)
    # 中间 = msgs[1],content 短
    assert out[1] == {"role": "user", "content": "short middle msg"}
