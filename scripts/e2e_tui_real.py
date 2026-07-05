#!/usr/bin/env python3
"""U4: CLI 真 stdin RPC 端到端验证。

用 subprocess.Popen + 同步 readline (不用 asyncio subprocess, 避免 selector fd 问题)。

P1 plan U4:
  spawn backend 子进程 → stdin 发 JSON-RPC agent.run("op: ...") →
  监听 stdout agent.progress 通知 → 断言 PhaseRunner 走通。

用法::

    PYTHONPATH=src python scripts/e2e_tui_real.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PYTHON = sys.executable


def run_e2e_tui(timeout: int = 300) -> int:
    """spawn backend 子进程 + stdin RPC + 监听 stdout + 断言。"""

    print("=" * 60)
    print("🚀 U4 CLI 真 stdin RPC 端到端验证")
    print("=" * 60)

    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(REPO_ROOT / "src")}

    # P1 U4 fix: 写独立 config 文件 (auto_resume=False + 独立 db) 避免污染 ~/.ascend_op_agent
    # 之前 N=20 stress 留下 9 个 pending checkpoint, 启动时 _resume_pending_check 加载它们标 failed
    # 从 ~/.ascend_op_agent/config.yaml 读真实 llm 配置, 写 tmp 文件改 db_path + auto_resume
    src_cfg = Path("/Users/huangshilei/.ascend_op_agent/config.yaml")
    fresh_cfg = REPO_ROOT / "tmp_e2e_config.yaml"
    fresh_cfg.parent.mkdir(parents=True, exist_ok=True)
    if src_cfg.exists():
        cfg_text = src_cfg.read_text(encoding="utf-8")
    else:
        cfg_text = "llm: {provider: anthropic, api_key: '', api_base: 'https://api.minimaxi.com/anthropic', model: 'MiniMax-M3', max_retries: 3, timeout: 300}\nremote: {host: '192.168.9.105', user: 'root', port: 22, container_name: 'ops_pt'}\n"
    # 在 llm 块后 + remote 块前插入 checkpoint
    fresh_cfg.write_text(
        cfg_text + "\ncheckpoint:\n  db_path: \"/tmp/e2e_u4_checkpoints.db\"\n  auto_resume: false\n",
        encoding="utf-8",
    )
    env["ASCEND_OP_AGENT_CONFIG"] = str(fresh_cfg)

    task_input = "op: 实现一个 AscendC 算子:对两个 [16,16] float32 张量做逐元素 add。"
    rpc_id = int(time.time())

    print(f"\n[1] spawning backend: {PYTHON} -m ascend_op_agent.backend")
    proc = subprocess.Popen(
        [PYTHON, "-m", "ascend_op_agent.backend"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(REPO_ROOT),
        text=True,
        bufsize=1,  # line-buffered
    )
    print(f"    PID={proc.pid}")

    # stdout / stderr reader threads
    notifications: list[dict] = []
    rpc_responses: list[dict] = []
    backend_ready = threading.Event()
    stderr_lines: list[str] = []
    all_done = threading.Event()

    def read_stdout():
        """逐行读 stdout, 分拣 notification vs RPC response。"""
        for line in proc.stdout:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            method = msg.get("method")
            if method == "backend.ready":
                backend_ready.set()
                print(f"    [recv] backend.ready")
            elif method:
                notifications.append(msg)
                params = msg.get("params") or {}
                phase = params.get("phase") or (params.get("payload") or {}).get("phase", "?")
                event = params.get("event") or (params.get("payload") or {}).get("event", "?")
                print(f"    [recv] {method} phase={phase} event={event}")
            elif "result" in msg or "error" in msg:
                rpc_responses.append(msg)
                print(f"    [recv] RPC response id={msg.get('id')}")
                all_done.set()

    def read_stderr():
        """读 stderr (backend 日志, 调试用)。"""
        for line in proc.stderr:
            stderr_lines.append(line.strip())

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    # 等 backend.ready (最多 30s)
    print(f"\n[2] waiting for backend.ready (max 30s)...")
    if not backend_ready.wait(timeout=30):
        print("    [FAIL] backend.ready timeout (30s)")
        print(f"    stderr tail: {' '.join(stderr_lines[-5:])}")
        proc.kill()
        return 1
    print("    [OK] backend ready")

    # 发 agent.run RPC
    print(f"\n[3] sending agent.run: {task_input[:60]}...")
    rpc_request = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "agent.run",
        "params": {"user_input": task_input},
    }
    try:
        proc.stdin.write(json.dumps(rpc_request) + "\n")
        proc.stdin.flush()
    except BrokenPipeError:
        print("    [FAIL] stdin pipe broken (backend crashed?)")
        print(f"    stderr tail: {' '.join(stderr_lines[-5:])}")
        return 1

    # 等完成（收到 RPC response 或 timeout）
    print(f"\n[4] waiting for RPC response (max {timeout}s)...")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if all_done.is_set():
            break
        if proc.poll() is not None:
            print(f"    [WARN] backend exited with code {proc.returncode}")
            break
        time.sleep(1)
    else:
        print(f"    [FAIL] RPC response timeout ({timeout}s)")
        proc.kill()
        return 1

    elapsed = time.time() - t0

    # 断言结果
    print(f"\n[5] asserting results (elapsed {elapsed:.0f}s)...")
    pass_count = 0
    fail_count = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal pass_count, fail_count
        status = "✅" if ok else "❌"
        print(f"    {status} {name}: {detail}")
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    # 1. agent.progress notification 至少 1 个
    check(
        "agent.progress notifications received",
        len(notifications) >= 1,
        f"{len(notifications)} notifications",
    )

    # 2. PhaseRunner phase 推进
    phases_seen = set()
    for n in notifications:
        params = n.get("params") or {}
        phase = params.get("phase") or (params.get("payload") or {}).get("phase")
        if phase:
            phases_seen.add(phase)
    check(
        "PhaseRunner phases advanced",
        len(phases_seen) >= 2,
        f"phases: {sorted(phases_seen)}",
    )

    # 3. RPC response
    response = next((r for r in rpc_responses if r.get("id") == rpc_id), None)
    if response:
        print(f"    DEBUG raw response: {json.dumps(response, ensure_ascii=False)[:2000]}")
        if "error" in response:
            check("RPC response status", False, f"error: {response['error']}")
        else:
            result = response.get("result") or {}
            status = result.get("status", "?")
            data = result.get("data") or {}
            thread_id = data.get("thread_id")
            current_phase = data.get("current_phase")
            check(
                "RPC response status",
                status in ("completed", "interrupted"),
                f"status={status}",
            )
            check("thread_id generated", bool(thread_id), f"thread_id={thread_id}")
            check(
                "PhaseRunner reached expected phase",
                # interrupted: current_phase = 被打断的阶段 (设计/审查);
                # completed: current_phase = done 或 framework_adapt。
                # 这里只校验至少有 advanced 到了 design, 表明 PhaseRunner 真走了
                # (compile / precision 等要求 user 输入 HITL, 在 auto-e2e 测里不现实)。
                current_phase in (
                    "design", "review_fix", "compile", "precision",
                    "delivery_mode", "framework_adapt", "done",
                ),
                f"current_phase={current_phase}",
            )
    else:
        check("RPC response received", False, "no response (HITL interrupted?)")

    # 关闭 backend
    print(f"\n[6] shutting down backend (PID={proc.pid})...")
    try:
        proc.stdin.write(
            '{"jsonrpc":"2.0","id":999,"method":"session.shutdown","params":{}}\n'
        )
        proc.stdin.flush()
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
        proc.kill()
    t_out.join(timeout=3)

    # 总结
    print(f"\n{'=' * 60}")
    if fail_count == 0:
        print(f"🚢 U4 PASS — {pass_count} checks passed, 0 failed")
        print("=" * 60)
        return 0
    else:
        print(f"❌ U4 FAIL — {pass_count} passed, {fail_count} failed")
        if stderr_lines:
            print(f"    stderr tail: {' '.join(stderr_lines[-5:])}")
        print("=" * 60)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="U4 CLI 真 stdin RPC 端到端")
    parser.add_argument("--timeout", type=int, default=300,
                        help="RPC response timeout (sec, default 300)")
    args = parser.parse_args()
    return run_e2e_tui(timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
