"""U14: 闭环修复控制器 + 上下文压缩。

设计:

1. ``run_fix_loop(state, kind, ...)``:review→fix→re-review 循环
   - ``kind``: ``"compile"`` / ``"precision"`` 决定 review_node + fix_node 的 prompt
   - 停止条件:review clean(无问题) / max_rounds / 不可修复信号(确定性 fix 失败)
   - 终态:``status="done"``(clean) 或 ``status="failed"``(max_rounds/不可修复)
2. ``compress_transcript(messages, keep_last_n)``:token 压缩
   - 保留最近 N 轮对话 + 第一条(系统/初始需求)
   - 中间消息:保留 role + content 前 200 字摘要,工具结果丢弃

不依赖 LLM API key 也不依赖硬件 —— review/fix 节点是 generic,调用方注入。
本模块只负责 loop 控制 + 收敛检测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ascend_op_agent.orchestrator.state_machine import Node


logger = logging.getLogger(__name__)


# ---- review 结果契约 ----


@dataclass
class ReviewResult:
    """review 节点输出契约。

    review_node 返回 ``{"clean": bool, "issues": [...], "fatal": bool}``:
    - ``clean=True``:无问题,fix_loop 终止(status=done)
    - ``clean=False, fatal=False``:有可修复问题,继续 fix
    - ``clean=False, fatal=True``:确定性不可修复(如缺 dtype 支持),终止 status=failed
    """

    clean: bool
    fatal: bool = False
    issues: list[str] = field(default_factory=list)
    raw_response: str = ""


# ---- run_fix_loop ----


FixNodeFunc = Callable[[dict, list[str]], dict]
ReviewNodeFunc = Callable[[dict], ReviewResult]


def run_fix_loop(
    state: dict,
    kind: str,
    review_func: ReviewNodeFunc,
    fix_func: FixNodeFunc,
    max_rounds: int = 5,
    fatal_keywords: Optional[tuple[str, ...]] = None,
) -> dict:
    """闭环修复循环。

    Args:
        state: OpState dict(可读 current_phase / 上轮 fix 产物)
        kind: ``"compile"`` / ``"precision"``,日志/错误消息用
        review_func: ``callable(state) -> ReviewResult`` —— 给定 state 返回 review
        fix_func: ``callable(state, issues) -> dict`` —— 给定 state + issues 返回
            修复后的 update dict(写回 state 由调用方 apply)
        max_rounds: 最多几轮 review→fix
        fatal_keywords: review.raw_response 命中时升级为 fatal(不可修复)

    Returns:
        ``{"status": "done"|"failed", "rounds": int, "clean": bool, "reason": str}``

    停止条件:

    - review.clean == True → ``status="done", reason="clean"``
    - review.fatal == True 或 raw_response 命中 fatal_keywords →
      ``status="failed", reason="fatal"``
    - rounds >= max_rounds → ``status="failed", reason="max_rounds"``
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")
    if kind not in ("compile", "precision"):
        logger.warning(f"Unknown fix_loop kind: {kind} (expected compile/precision)")

    fatal_kws = fatal_keywords or (
        "unsupported dtype",
        "missing hardware feature",
        "cannot be implemented",
    )

    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        logger.info(f"fix_loop[{kind}] round={rounds}/{max_rounds}")

        review = review_func(state)

        # fatal keyword 升级
        if not review.fatal and review.raw_response:
            for kw in fatal_kws:
                if kw.lower() in review.raw_response.lower():
                    review = ReviewResult(
                        clean=False,
                        fatal=True,
                        issues=review.issues + [f"fatal keyword: {kw}"],
                        raw_response=review.raw_response,
                    )
                    break

        if review.clean:
            return {
                "status": "done",
                "rounds": rounds,
                "clean": True,
                "reason": "clean",
                "last_review": review.raw_response,
            }

        if review.fatal:
            return {
                "status": "failed",
                "rounds": rounds,
                "clean": False,
                "reason": "fatal",
                "last_review": review.raw_response,
                "issues": review.issues,
            }

        # 跑 fix
        update = fix_func(state, review.issues)
        # 把 fix update 应用到 state(让下一轮 review 看到修复后的产物)
        for key, value in update.items():
            if isinstance(value, dict) and isinstance(state.get(key), dict):
                state[key] = {**state[key], **value}
            else:
                state[key] = value

        logger.info(f"fix_loop[{kind}] round={rounds} applied fix, re-reviewing")

    # max_rounds 用完
    return {
        "status": "failed",
        "rounds": rounds,
        "clean": False,
        "reason": "max_rounds",
        "last_review": review.raw_response,
        "issues": review.issues,
    }


def make_fix_loop_node(
    phase: str,
    kind: str,
    review_node: Node,
    fix_node: Node,
    max_rounds: int = 5,
    fatal_keywords: Optional[tuple[str, ...]] = None,
) -> Node:
    """把 run_fix_loop 包成 PhaseRunner 节点。

    Args:
        phase: 节点名(如 ``"compile_fix_loop"`` / ``"precision_fix_loop"``)
        kind: ``"compile"`` / ``"precision"``
        review_node: Node —— ``func(state) -> dict``,dict 内应有 ``clean`` / ``fatal``
            / ``issues`` / ``raw_response`` 字段(对应 ReviewResult)
        fix_node: Node —— ``func(state) -> dict``,修复后的 update
        max_rounds: 最多几轮
        fatal_keywords: 触发 fatal 的关键字

    Returns:
        Node —— 跑完写 ``state[f"{phase}_result"]`` 含 status/rounds/reason
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    def _review_func(s: dict) -> ReviewResult:
        update = review_node.func(s)
        return ReviewResult(
            clean=bool(update.get("clean", False)),
            fatal=bool(update.get("fatal", False)),
            issues=list(update.get("issues", []) or []),
            raw_response=str(update.get("raw_response", update.get("response", ""))),
        )

    def _fix_func(s: dict, issues: list[str]) -> dict:
        # 把 issues 写回 state 给 fix_node 看(避免修改 fix_node 接口)
        s["_fix_loop_issues"] = issues
        return fix_node.func(s)

    def _loop_node(state: dict) -> dict:
        result = run_fix_loop(
            state=state,
            kind=kind,
            review_func=_review_func,
            fix_func=_fix_func,
            max_rounds=max_rounds,
            fatal_keywords=fatal_keywords,
        )
        # 清理临时字段
        state.pop("_fix_loop_issues", None)
        return {f"{phase}_result": result}

    return Node(name=phase, func=_loop_node)


# ---- compress_transcript ----


_SUMMARY_LEN = 200


def compress_transcript(
    messages: list[dict],
    keep_last_n: int = 4,
) -> list[dict]:
    """压缩 messages:保留首条 + 最近 N 条,中间走摘要。

    Args:
        messages: ``[{role, content}, ...]``
        keep_last_n: 保留最后几条原消息

    Returns:
        压缩后的 messages 列表

    规则:

    - 总长 <= keep_last_n + 1(含首条):原样返回
    - 否则:首条 + 中间(每条保留 role + content 摘要,工具结果丢弃) +
      最后 keep_last_n 条
    """
    if not isinstance(messages, list) or len(messages) <= keep_last_n + 1:
        return list(messages) if isinstance(messages, list) else []

    first = messages[0]
    tail = messages[-keep_last_n:]
    middle = messages[1:-keep_last_n]

    compressed = [first]
    for msg in middle:
        if not isinstance(msg, dict):
            continue
        # 跳过工具结果(role=tool 或 role=function 的 content 通常是大块 stdout)
        role = msg.get("role", "")
        if role in ("tool", "function"):
            continue
        content = str(msg.get("content", ""))
        if len(content) > _SUMMARY_LEN:
            content = content[:_SUMMARY_LEN] + "... [compressed]"
        compressed.append({"role": role, "content": content})
    compressed.extend(tail)
    return compressed
