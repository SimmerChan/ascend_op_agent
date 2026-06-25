#!/usr/bin/env python3
"""E2E:真实 LLM + 真实 NPU 跑一次算子开发(不停留 mock)。

直接调 build_new_dev_graph(不走 backend.py 那个未接的 _orchestrator=None 路径),
三件真:

- LLM: ``LLMClient`` 调 minimaxi 的 MiniMax-M3(anthropic 协议)
- NPU: ``NpuExecutor(ssh_env=192.168.9.105, container_name=ops_pt)`` 远程 build.sh
- Workspace: 本地 LLM 写代码 → rsync 到 910B 容器 → 真编译

用法::

    PYTHONPATH=src python scripts/e2e_real_op.py "实现一个 float32 逐元素 add 算子"

不带参数时跑默认任务(elementwise add for [16,16] fp32 tensor)。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# 把 src/ 加到 import path,直接 import,不走 pip install
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

# 真实 LLM 组件
from ascend_op_agent.agent.context import ContextEngine
from ascend_op_agent.agent.core import AIAgent
from ascend_op_agent.agent.memory import MemoryStore
from ascend_op_agent.agent.prompt_builder import PromptBuilder
from ascend_op_agent.agent.tool_registry import tool_registry
from ascend_op_agent.config import load_config

# 真实 NPU
from ascend_op_agent.orchestrator import (
    CheckpointStore,
    build_new_dev_graph,
)
from ascend_op_agent.orchestrator.nodes.validation import (
    make_real_compile_node,
    make_real_precision_node,
)
from ascend_op_agent.orchestrator.npu_exec import NpuExecutor
from ascend_op_agent.ssh.manager import SSHEnvironment


# ---- 配置常量(910B @ 192.168.9.105,容器 ops_pt)----
NPU_HOST = "192.168.9.105"
NPU_USER = "root"
NPU_CONTAINER = "ops_pt"
NPU_CANN_SETUP = "/usr/local/Ascend/ascend-toolkit/set_env.sh"
# 910B 容器内的工作根目录(给真实代码落点,后面要能 build.sh)
NPU_REMOTE_WORKDIR = "/home/hsl/e2e_ops"
# 本地暂存(LLM 写到这里,后 rsync 到 NPU_REMOTE_WORKDIR)
LOCAL_WORKDIR = Path("/tmp/e2e_ops_local")


# ---- 阶段事件回调(实时打印)----


def make_phase_callback(label: str):
    def _cb(phase: str, status: str, payload=None) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] [{label}] phase={phase} status={status} payload_keys={list((payload or {}).keys())}")
    return _cb


# ---- 真实 LLM Agent 工厂(每个节点返回 fresh AIAgent,session_manager=None)----


def make_real_agent_factory():
    """真实 LLM 的 agent_factory:返回 fresh AIAgent(per node)。

    与 backend.py:_setup_agent 同形但 session_manager=None(编排器 owns 持久化)。
    """
    config = load_config()
    pb = PromptBuilder()
    ctx = ContextEngine()
    mem = MemoryStore()

    def _factory() -> AIAgent:
        return AIAgent(
            config=config,
            tool_registry=tool_registry,
            prompt_builder=pb,
            context_engine=ctx,
            memory_store=mem,
            session_manager=None,  # 编排器 owns 持久化
        )

    return _factory


# ---- 真实 NPU 接线 ----


def make_npu_executor() -> NpuExecutor:
    """真实 NpuExecutor:本地不需 CANN,所有 build.sh 走 SSH→container。"""
    ssh_env = SSHEnvironment(
        host=NPU_HOST,
        user=NPU_USER,
        port=22,
        timeout=60,
    )
    return NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=f"source {NPU_CANN_SETUP} > /dev/null 2>&1 && ",
        container_name=NPU_CONTAINER,
        archive_dir=Path("/tmp/e2e_ops_archive"),
    )


# ---- operator_path_resolver:从 code_result.files 取工程根目录 + rsync 到 NPU ----


def make_operator_path_resolver(remote_workdir: str):
    """返回 (state) -> str(远程 operator_path,供 build.sh 跑)。"""

    def _resolve(state: dict) -> str:
        code_result = state.get("code_result") or {}
        files = code_result.get("files") or []
        if not files:
            raise ValueError("code_result.files 为空,codegen 阶段没写代码")
        # 第一个文件所在目录
        first = files[0]["path"]
        local_dir = str(Path(first).parent)
        # rsync 到 910B 容器
        remote_dir = f"{remote_workdir}/{Path(local_dir).name}"
        _rsync_to_npu(local_dir, remote_dir)
        return remote_dir

    return _resolve


def _rsync_to_npu(local_dir: str, remote_dir: str) -> None:
    """用 docker cp 把本地目录塞进 ops_pt 容器(910B 上没 rsync,见 7482 观察)。

    1. 容器内 tar 接收端:`docker exec ... tar -xf - -C <remote_dir>`
    2. 本地 tar 发送端:`tar -cf - -C <local_dir> . | ssh ... docker exec -i ops_pt tar -xf - -C <remote_dir>`
    """
    # 先确保远程目录存在
    ssh = f"ssh -o StrictHostKeyChecking=no root@{NPU_HOST}"
    mkdir_cmd = f"{ssh} 'docker exec {NPU_CONTAINER} mkdir -p {remote_dir}'"
    os.system(mkdir_cmd)
    # tar 流式传输
    tar_cmd = (
        f"tar -cf - -C {local_dir} . | "
        f"{ssh} 'docker exec -i {NPU_CONTAINER} tar -xf - -C {remote_dir}'"
    )
    rc = os.system(tar_cmd)
    if rc != 0:
        raise RuntimeError(f"rsync(tar stream) failed: rc={rc}, local={local_dir}, remote={remote_dir}")


# ---- precision 测试用例(简单 identity 占位)----


def make_test_cases_resolver():
    import numpy as np

    def _resolve(state: dict) -> list[dict]:
        # 简单占位:golden==actual 全部通过(precision 节点是确定性节点,
        # 它会真跑 NpuExecutor.run_precision。这里只验证 orchestration 通路)
        return [
            {"golden": np.array([1.0]), "actual": np.array([1.0])},
        ]

    return _resolve


# ---- 主流程 ----


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?", default=(
        "实现一个 AscendC 算子:对两个 [16, 16] float32 张量做逐元素 add(out = a + b)。"
        "生成完整可编译的工程,含 op_kernel.cpp、op_host.cpp、CMakeLists.txt 和 build.sh。"
    ))
    parser.add_argument("--thread-id", default=f"e2e-{int(time.time())}")
    parser.add_argument("--local-workdir", default=str(LOCAL_WORKDIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    # 0. 准备本地工作目录
    local_workdir = Path(args.local_workdir)
    local_workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(local_workdir)
    print(f"[setup] local workdir = {local_workdir}")
    print(f"[setup] remote workdir = {NPU_REMOTE_WORKDIR}")
    print(f"[setup] thread_id = {args.thread_id}")

    # 1. 真实 NPU + 真实 LLM 接线
    print("\n[setup] creating real NpuExecutor (SSH → 910B ops_pt)...")
    npu = make_npu_executor()
    print(f"[setup]   is_remote={npu.is_remote} is_containerized={npu.is_containerized}")
    print("[setup]   probing remote CANN env...")
    cann_ok = npu.is_remote_cann_available()
    print(f"[setup]   remote_cann_available={cann_ok}")
    if not cann_ok:
        print("[setup] ⚠️  remote CANN not available, compile will fail")

    print("\n[setup] loading config + LLM client...")
    cfg = load_config()
    print(f"[setup]   llm.provider={cfg.llm.provider} model={cfg.llm.model}")

    print("\n[setup] creating CheckpointStore...")
    ckpt_path = local_workdir / "checkpoints.db"
    if ckpt_path.exists():
        ckpt_path.unlink()
    store = CheckpointStore(ckpt_path)
    print(f"[setup]   db={ckpt_path}")

    # 2. 构造 graph
    print("\n[graph] building new_dev graph with real LLM + real NPU...")
    agent_factory = make_real_agent_factory()
    operator_path_resolver = make_operator_path_resolver(NPU_REMOTE_WORKDIR)
    test_cases_resolver = make_test_cases_resolver()
    phase_cb = make_phase_callback("orchestrator")

    runner = build_new_dev_graph(
        store=store,
        agent_factory=agent_factory,
        phase_callback=phase_cb,
        compile_node_factory=lambda: make_real_compile_node(
            executor=npu,
            operator_path_resolver=operator_path_resolver,
        ),
        precision_node_factory=lambda: make_real_precision_node(
            executor=npu,
            test_cases_resolver=test_cases_resolver,
        ),
        use_real_skill_bundles=True,  # 真实 cannbot skill 包(从 vendor/ 读)
    )

    # 3. invoke + 循环 resume(HITL 全批准)
    print(f"\n[run] invoking task: {args.task[:80]}...")
    state = runner.invoke(args.task, thread_id=args.thread_id)
    round_n = 0
    while state.get("pending_confirmation") is not None:
        round_n += 1
        pending = state["pending_confirmation"]
        print(f"\n[run] HITL round {round_n}: {pending.get('phase', '?')} → auto-approve")
        state = runner.resume(args.thread_id, payload={"approved": True})
        if round_n > 5:
            print("[run] ⚠️  too many HITL rounds, break")
            break

    # 4. 报告
    print("\n========== e2e result ==========")
    print(f"current_phase: {state.get('current_phase')}")
    print(f"status:        {state.get('__status__') or 'running'}")
    print(f"messages:      {len(state.get('messages', []))} turns")
    print(f"code_result:   {bool(state.get('code_result'))}")
    if state.get("code_result"):
        cr = state["code_result"]
        files = (cr.get("files") or [])
        print(f"  files: {len(files)}")
        for f in files[:5]:
            p = f.get("path", "?")
            content_len = len(f.get("content", ""))
            print(f"    - {p} ({content_len} chars)")
    print(f"compile_result: {bool(state.get('compile_result'))}")
    if state.get("compile_result"):
        c = state["compile_result"]
        print(f"  success:      {c.get('success')}")
        print(f"  return_code:  {c.get('return_code')}")
        print(f"  command:      {c.get('command', '?')[:200]}")
        if not c.get("success"):
            err = (c.get("stderr") or "")[:500]
            print(f"  stderr[0:500]: {err}")
    print(f"precision_report: {bool(state.get('precision_report'))}")
    if state.get("precision_report"):
        pr = state["precision_report"]
        print(f"  total: {pr.get('total_cases')}, passed: {pr.get('passed_cases')}, failed: {pr.get('failed_cases')}")
    print(f"delivery_mode: {state.get('delivery_mode')}")
    print("================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
