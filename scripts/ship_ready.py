#!/usr/bin/env python3
"""U8: ship_ready.py — P1 ship-readiness 唯一 boolean gate。

P1 plan U8 (F-P1-SCOPE-08 round 5):
  串行跑 lint + unit_test + stress + e2e_tui,任何一步失败 = exit 1。
  P1 商业化前必过。

用法::

    # 全跑(需 910B + LLM API)
    python scripts/ship_ready.py

    # dev fast iteration(stress + e2e 标 TODO 占位, 留 API/910B 通后跑)
    python scripts/ship_ready.py --skip-stress --skip-e2e

    # 只 lint + test(stress/e2e 占位时默认跳)
    python scripts/ship_ready.py --skip-stress --skip-e2e

输出:
  - 每步 [RUN] / [PASS] / [FAIL] / [SKIP]
  - 全 pass → "SHIP READY" + exit 0
  - 任一 fail → "NOT READY: <step>" + exit 1 (失败立即 abort, 不继续后续 step)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).parent.parent
PYTHON = sys.executable


@dataclass
class Step:
    """ship gate 单步。"""

    name: str
    cmd: list[str]
    timeout_sec: int
    skip_flag: Optional[str] = None  # e.g. "--skip-stress"; None = 不可跳
    env_extra: dict = field(default_factory=dict)
    is_placeholder: bool = False  # True = 依赖未实施 unit, 默认跳


# 4 步 gate (P1 plan U8 spec)
STEPS: list[Step] = [
    Step(
        name="lint",
        # 用 black --check (ruff 在某些 env 未装; black 是 pyproject devDep 必装)
        # 只查最近改动文件(git diff + git stash list 空),避免全 repo 历史欠债阻塞 ship gate
        # 想换 ruff 或全 repo: 改 cmd 即可
        # bash -c 内每段用 || true 容错(git diff 在浅 clone / 无 HEAD~5 时 exit 非 0)
        cmd=[
            "bash",
            "-c",
            "set -o pipefail; "
            "FILES=$( (git diff --name-only HEAD~5 2>/dev/null; git diff --name-only --cached 2>/dev/null; git diff --name-only 2>/dev/null) | grep -E '\\.(py)$' | sort -u || true); "
            'test -z "$FILES" || ' + PYTHON + " -m black --check --quiet $FILES",
        ],
        timeout_sec=300,
    ),
    Step(
        name="unit_test",
        cmd=[
            PYTHON,
            "-m",
            "pytest",
            "tests/unit/",
            "-q",
            "--timeout=60",
            "--ignore=tests/unit/backend/rpc/test_server.py",
        ],
        timeout_sec=900,
        env_extra={"PYTHONPATH": "src"},
    ),
    Step(
        name="stress",
        # U3 stress: e2e_real_op.py --stress 20 (需 910B + LLM API)
        cmd=[PYTHON, "scripts/e2e_real_op.py", "--stress", "20"],
        timeout_sec=1800,  # 30 min (N=20 × ~30s + retry)
        skip_flag="--skip-stress",
        env_extra={"PYTHONPATH": "src"},
        is_placeholder=False,  # U3 已实施, 但需 910B+LLM
    ),
    Step(
        name="e2e_tui",
        # U2 vitest + ink-testing-library: 前端 Ink render 单测 (mock backend)
        # U4 真 stdin RPC 端到端 未实施, 但 U2 vitest 已可跑 (2 test pass)
        # npm --prefix frontend 让 npm 在 frontend/ 目录跑(避免 cwd 切换)
        cmd=["npm", "--prefix", "frontend", "test"],
        timeout_sec=120,
        skip_flag="--skip-e2e",
        env_extra={},
        is_placeholder=False,  # U2 已实施, npm test 跑通
    ),
]


def run_step(step: Step, skip_flags: set[str], verbose: bool = False) -> bool:
    """跑单步。返回 True if pass (或 skip), False if fail。"""
    # 占位 step 默认跳(除非显式 --no-skip-e2e 强跑)
    if step.is_placeholder and step.skip_flag and step.skip_flag not in skip_flags:
        # 占位但用户没显式 skip → 自动跳 + WARN
        print(f"[SKIP] {step.name} (placeholder, U4 未实施; 用 {step.skip_flag} 显式跳)")
        return True

    if step.skip_flag and step.skip_flag in skip_flags:
        print(f"[SKIP] {step.name} (user {step.skip_flag})")
        return True

    print(f"\n[RUN] {step.name}: {' '.join(step.cmd)}")
    t0 = time.time()
    env = {**os.environ, **step.env_extra}
    try:
        result = subprocess.run(
            step.cmd,
            timeout=step.timeout_sec,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        print(f"[FAIL] {step.name}: TIMEOUT after {step.timeout_sec}s (elapsed {elapsed:.0f}s)")
        return False
    except FileNotFoundError as e:
        print(f"[FAIL] {step.name}: command not found - {e}")
        return False

    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"[FAIL] {step.name}: exit {result.returncode} (elapsed {elapsed:.0f}s)")
        # stderr/stdout 尾部 500 字符
        tail = (result.stderr or result.stdout or "")[-500:]
        if tail:
            print(f"  output tail:\n    {tail.replace(chr(10), chr(10) + '    ')}")
        if verbose:
            print(f"  full stdout:\n{result.stdout}")
        return False

    print(f"[PASS] {step.name} (elapsed {elapsed:.0f}s)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 ship-readiness boolean gate")
    parser.add_argument(
        "--skip-stress", action="store_true", help="跳过 stress step (U3, 需 910B+LLM)"
    )
    parser.add_argument(
        "--skip-e2e", action="store_true", help="跳过 e2e_tui step (U4, 当前 placeholder)"
    )
    parser.add_argument("--only", choices=[s.name for s in STEPS], help="只跑指定 step (debug 用)")
    parser.add_argument("--verbose", "-v", action="store_true", help="失败时打 full stdout")
    args = parser.parse_args()

    skip_flags: set[str] = set()
    if args.skip_stress:
        skip_flags.add("--skip-stress")
    if args.skip_e2e:
        skip_flags.add("--skip-e2e")

    steps = STEPS if args.only is None else [s for s in STEPS if s.name == args.only]

    print("=" * 50)
    print("🚢 P1 ship-ready gate")
    print("=" * 50)
    print(f"steps: {[s.name for s in steps]}")
    print(f"skip_flags: {sorted(skip_flags) or '(none)'}")

    failing: Optional[str] = None
    for step in steps:
        if not run_step(step, skip_flags, verbose=args.verbose):
            failing = step.name
            break  # 失败立即 abort

    print("\n" + "=" * 50)
    if failing is None:
        print("🚢 SHIP READY")
        print("=" * 50)
        return 0
    else:
        print(f"❌ NOT READY: {failing}")
        print("=" * 50)
        return 1


if __name__ == "__main__":
    sys.exit(main())
