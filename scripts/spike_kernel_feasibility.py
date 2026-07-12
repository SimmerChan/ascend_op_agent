#!/usr/bin/env python3
"""Path A U1 spike: Pre-build kernel feasibility spike.

量化 LLM 写 AscendC kernel 的 ST pass rate(plan U1 R14,gate "不分期" 决策)。

从 demo model 采 N(≥5)个真实 custom CUDA op,逐个调现有路径 B
(``build_new_dev_graph`` 全流程: codegen → 910B 编译 → ST 精度),
统计 ST pass rate。复用 ``scripts/e2e_real_op.py`` 的 LLM + NPU 接线模式。

GO/NO-GO:
- pass_rate >= 50% → U4/U8 全力推进(Path A 继续)
- pass_rate < 50%  → 触发"不分期"重开对话(停 U4/U8)

Hardware-gated: 910B SSH 不可达 → spike 早退 + 明确报错(不挂起)。

用法::

    PYTHONPATH=src python scripts/spike_kernel_feasibility.py                # 5 default ops
    PYTHONPATH=src python scripts/spike_kernel_feasibility.py --op vector_add  # 单个 op
    PYTHONPATH=src python scripts/spike_kernel_feasibility.py --list          # 列 op 列表
    PYTHONPATH=src python scripts/spike_kernel_feasibility.py --dry-run       # 不真跑,只列 ops
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

# ---- 910B 配置(镜像 scripts/e2e_real_op.py)----
NPU_HOST = "192.168.9.105"
NPU_USER = "root"
NPU_CONTAINER = "ops_pt"
NPU_CANN_SETUP = "/usr/local/Ascend/ascend-toolkit/set_env.sh"
NPU_REMOTE_WORKDIR = "/home/hsl/e2e_ops_spike"
LOCAL_WORKDIR = Path("/tmp/e2e_spike_local")
SCAFFOLD_DIR = Path("/tmp/e2e_scaffold")  # 参考工程(已验证可编译 add_example)

PASS_THRESHOLD = 0.50  # plan U1 GO 阈值


# ---- 5 demo custom CUDA ops(从 cannbot-skills fixture 抽)----

DEMO_OPS = {
    "vector_add": (
        "实现一个 AscendC 算子:对两个 [16, 16] float32 张量做逐元素 add"
        "(out = a + b)。生成完整可编译的工程,含 op_kernel.cpp、op_host.cpp、"
        "CMakeLists.txt 和 build.sh。"
    ),
    "muladd": (
        "实现一个 AscendC 算子:fused multiply-add,对两个 [16, 16] float32 张量"
        "和标量 alpha,计算 out = alpha * a + b (element-wise)。"
        "生成完整工程,含 op_kernel.cpp、op_host.cpp、CMakeLists.txt 和 build.sh。"
    ),
    "relu": (
        "实现一个 AscendC 算子:ReLU 激活函数,对 [16, 16] float32 张量逐元素"
        "计算 out = max(0, x)。生成完整工程,含 op_kernel.cpp、op_host.cpp、"
        "CMakeLists.txt 和 build.sh。"
    ),
    "dot_product": (
        "实现一个 AscendC 算子:点积,对两个长度为 256 的 float32 向量计算"
        "内积输出标量。生成完整工程,含 op_kernel.cpp、op_host.cpp、"
        "CMakeLists.txt 和 build.sh。"
    ),
    "softmax": (
        "实现一个 AscendC 算子:Softmax,对长度 256 的 float32 向量计算"
        "softmax 归一化输出同长度向量。生成完整工程,含 op_kernel.cpp、"
        "op_host.cpp、CMakeLists.txt 和 build.sh。"
    ),
}


@dataclass
class OpResult:
    """单个 op 的 spike 结果。"""

    name: str
    description: str
    compile_success: bool = False
    precision_success: bool = False
    stderr_summary: str = ""
    exception: str = ""
    duration_sec: float = 0.0
    threads_total: int = 0
    threads_failed: int = 0

    @property
    def passed(self) -> bool:
        return self.compile_success and self.precision_success


def setup_scaffold_once(local_workdir: Path) -> None:
    """spike 模式: scaffold copy 一次(每个 op 复用同一参考工程)。"""
    # build/ 是 910B 上的编译产物 stale,从本地 scaffold 排除避免 race
    IGNORE_NAMES = {"build", "__pycache__", ".git"}

    if local_workdir.exists():
        shutil.rmtree(local_workdir)
    op_dir = local_workdir / "op_demo"
    op_dir.mkdir(parents=True, exist_ok=True)
    if SCAFFOLD_DIR.exists():
        for item in SCAFFOLD_DIR.iterdir():
            if item.name in IGNORE_NAMES:
                continue
            dest = op_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        print(f"[setup] scaffold copied from {SCAFFOLD_DIR} to {op_dir} (excluded {IGNORE_NAMES})")
    else:
        print(f"[setup] WARN SCAFFOLD_DIR={SCAFFOLD_DIR} 不存在, 从零编译模式")


# ---- 复用 e2e_real_op.py 的接线工厂 ----

def make_real_agent_factory():
    from ascend_op_agent.agent.context import ContextEngine
    from ascend_op_agent.agent.core import AIAgent
    from ascend_op_agent.agent.memory import MemoryStore
    from ascend_op_agent.agent.prompt_builder import PromptBuilder
    from ascend_op_agent.agent.tool_registry import tool_registry
    from ascend_op_agent.config import load_config

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
            session_manager=None,
        )
    return _factory


def make_npu_executor():
    from ascend_op_agent.orchestrator.npu_exec import NpuExecutor
    from ascend_op_agent.ssh.manager import SSHEnvironment

    ssh_env = SSHEnvironment(
        host=NPU_HOST, user=NPU_USER, port=22, timeout=60,
    )
    return NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=f"source {NPU_CANN_SETUP} > /dev/null 2>&1 && ",
        container_name=NPU_CONTAINER,
        archive_dir=Path("/tmp/e2e_spike_archive"),
    )


def make_phase_callback(label: str):
    def _cb(phase: str, status: str, payload=None) -> None:
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] [{label}] phase={phase} status={status} "
            f"payload_keys={list((payload or {}).keys())}"
        )
    return _cb


def make_operator_path_resolver():
    """复用 e2e_real_op.py 的 resolver,支持 scaffold fallback + tar 流传输。"""
    # 把项目根加到 sys.path,让 `from scripts.e2e_real_op import ...` 能解析
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.e2e_real_op import make_operator_path_resolver as _orig  # type: ignore
    return _orig(NPU_REMOTE_WORKDIR)


def run_one_op(
    op_name: str,
    op_desc: str,
    local_workdir: Path,
    npu,
    use_scaffold_codegen: bool = False,
) -> OpResult:
    """跑单个 op 的完整 pipeline: codegen → 910B compile → ST precision。

    use_scaffold_codegen=False(默认,真测 LLM):走 5 节点 LLM codegen,LLM 调
    file_write 写 op_kernel.cpp/op_host.cpp/CMakeLists.txt/build.sh/op_kernel.ini。
    use_scaffold_codegen=True:走硬编码 scaffold 复用(op_add,不调 LLM)—— 仅用于
    基线对比,不测 LLM 能力。
    """
    from ascend_op_agent.orchestrator import CheckpointStore, build_new_dev_graph
    from ascend_op_agent.orchestrator.nodes.validation import (
        make_real_compile_fix_loop_node,
        make_real_compile_node,
        make_real_precision_node,
    )

    result = OpResult(name=op_name, description=op_desc)
    t0 = time.time()

    # 每 op 独立 fresh CheckpointStore db
    op_dir = local_workdir / f"op_{op_name}"
    if op_dir.exists():
        shutil.rmtree(op_dir)
    op_dir.mkdir(parents=True, exist_ok=True)

    # use_scaffold_codegen=False 时,5 节点 LLM codegen 硬编码 operator_dir 写入
    # /tmp/e2e_ops_local/op_add(见 new_dev.py:225)。确保该目录存在供 LLM file_write。
    # (path 不匹配是首次 spike 0/5 的根因:scaffold 节点读该路径但 spike 没建)
    if not use_scaffold_codegen:
        Path("/tmp/e2e_ops_local/op_add").mkdir(parents=True, exist_ok=True)

    if SCAFFOLD_DIR.exists():
        IGNORE_NAMES = {"build", "__pycache__", ".git"}
        for item in SCAFFOLD_DIR.iterdir():
            if item.name in IGNORE_NAMES:
                continue
            dest = op_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    # CheckpointStore 落 agent 目录(~/.ascend_op_agent/checkpoints/),不随 /tmp 清理丢失;
    # 每 run 独立文件带时间戳累积,viewer / skill 总结可反查历史。
    # scaffold 代码文件仍留 op_dir(/tmp,构建中间产物,rsync 到 910B 后即弃)。
    run_ts = int(time.time())
    ckpt_dir = Path("~/.ascend_op_agent/checkpoints").expanduser()
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = ckpt_dir / f"spike_{op_name}_{run_ts}.db"
    store = CheckpointStore(ckpt_path)

    thread_id = f"u1-spike-{op_name}-{run_ts}"
    print(f"\n[run] op={op_name} thread={thread_id}")

    try:
        runner = build_new_dev_graph(
            store=store,
            agent_factory=make_real_agent_factory(),
            phase_callback=make_phase_callback(f"{op_name}"),
            compile_node_factory=lambda: make_real_compile_node(
                executor=npu,
                operator_path_resolver=make_operator_path_resolver(),
            ),
            # U2 compile fix_loop:compile 失败 → LLM 修构建文件 → re-compile(内嵌)
            # 替代单 compile_node_factory,收敛 LLM 单次 codegen 偏差(spike #9 后)
            compile_fix_loop_node_factory=lambda: make_real_compile_fix_loop_node(
                executor=npu,
                operator_path_resolver=make_operator_path_resolver(),
                agent_factory=make_real_agent_factory(),
                max_rounds=3,
            ),
            precision_node_factory=lambda: make_real_precision_node(
                executor=npu,
                operator_path_resolver=make_operator_path_resolver(),
                operator_name_resolver=lambda s: (s.get("op_info") or {}).get(
                    "name", "add_example"
                ),
            ),
            use_scaffold_codegen=use_scaffold_codegen,
            use_real_skill_bundles=True,  # A 验证:接通 cannbot codegen skill(ascendc-direct-invoke-template + simt-best-practices)
        )

        state = runner.invoke(op_desc, thread_id=thread_id)

        # HITL resume(若有 pending_confirmation)
        round_n = 0
        while state.get("pending_confirmation") is not None:
            round_n += 1
            state = runner.resume(thread_id, payload={"approved": True})
            if round_n > 5:
                break

        # 提取结果
        cr = state.get("compile_result") or {}
        pr = state.get("precision_report") or {}
        result.compile_success = bool(cr.get("success"))
        result.precision_success = bool(pr.get("success"))
        if not result.compile_success:
            result.stderr_summary = (cr.get("stderr") or "")[:300]
        elif not result.precision_success:
            result.stderr_summary = f"precision: passed={pr.get('passed_cases')}/total={pr.get('total_cases')}"

        # 线程统计
        try:
            all_threads = npu.store.list_all_threads() if hasattr(npu, 'store') else []
            result.threads_total = len(all_threads)
        except Exception:
            pass

    except Exception as e:
        import traceback
        result.exception = f"{type(e).__name__}: {str(e)[:300]}"
        print(f"[run] op={op_name} EXCEPTION: {result.exception}")
        traceback.print_exc()
    finally:
        result.duration_sec = time.time() - t0

    return result


def run_spike(
    ops_to_run: list[tuple[str, str]],
    use_scaffold_codegen: bool = False,
) -> list[OpResult]:
    """跑全部 ops,continue-on-fail。

    use_scaffold_codegen 默认 False(真测 LLM 5 节点 codegen)。
    """
    print(f"[spike] running {len(ops_to_run)} ops")

    # 910B SSH 可达性 probe
    ssh = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 {NPU_USER}@{NPU_HOST}"
    rc = os.system(f"{ssh} 'echo probe_ok' > /dev/null 2>&1")
    if rc != 0:
        print(f"[spike] FATAL: 910B SSH unreachable ({NPU_HOST})")
        return [
            OpResult(
                name=name, description=desc,
                exception="ssh_unreachable",
            )
            for name, desc in ops_to_run
        ]

    # Real NpuExecutor 接线(整个 spike 共享一个)
    npu = make_npu_executor()
    cann_ok = npu.is_remote_cann_available()
    print(f"[spike] remote_cann_available={cann_ok}")
    if not cann_ok:
        print("[spike] WARN remote CANN not available, compile will fail")

    # Setup scaffold 一次
    setup_scaffold_once(LOCAL_WORKDIR)

    # 跑每个 op
    results: list[OpResult] = []
    for i, (op_name, op_desc) in enumerate(ops_to_run, 1):
        print(f"\n{'='*60}")
        print(f"[spike] [{i}/{len(ops_to_run)}] op={op_name}")
        print(f"{'='*60}")
        try:
            result = run_one_op(
                op_name, op_desc, LOCAL_WORKDIR, npu,
                use_scaffold_codegen=use_scaffold_codegen,
            )
        except Exception as e:
            result = OpResult(
                name=op_name, description=op_desc,
                exception=f"outer_exception: {type(e).__name__}: {str(e)[:200]}",
            )
        results.append(result)

    return results


def report(results: list[OpResult]) -> int:
    """汇总 + 输出 pass rate + GO/NO-GO 决策。返回 exit code。"""
    n = len(results)
    passed = sum(1 for r in results if r.passed)
    rate = passed / n if n else 0.0

    print("\n" + "=" * 60)
    print("U1 spike report")
    print("=" * 60)
    print(f"  ops total:    {n}")
    print(f"  passed:       {passed}")
    print(f"  failed:       {n - passed}")
    print(f"  pass rate:    {rate:.1%}  (threshold {PASS_THRESHOLD:.0%})")
    print()
    print("per-op detail:")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        extra = ""
        if r.exception:
            extra = f"  exception={r.exception[:80]}"
        elif r.stderr_summary:
            extra = f"  err={r.stderr_summary[:80]}"
        print(
            f"  - {r.name:14} {status:5} "
            f"({r.duration_sec:.1f}s, "
            f"compile={'Y' if r.compile_success else 'N'}, "
            f"precision={'Y' if r.precision_success else 'N'})"
            f"{extra}"
        )
    print()

    if rate >= PASS_THRESHOLD:
        print(f"GO: pass_rate {rate:.1%} >= {PASS_THRESHOLD:.0%} → U4/U8 全力推进")
        return 0
    else:
        print(
            f"NO-GO: pass_rate {rate:.1%} < {PASS_THRESHOLD:.0%} → "
            f"触发'不分期'重开对话(停 U4/U8, 回用户)"
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Path A U1 kernel feasibility spike")
    parser.add_argument(
        "--op", choices=list(DEMO_OPS.keys()),
        help="单个 op 跑(默认 5 个都跑)",
    )
    parser.add_argument(
        "--list", action="store_true", help="列 5 个 demo ops 及其描述",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="不真跑(910B),只列 ops + 输出 GO/NO-GO 阈值",
    )
    parser.add_argument(
        "--scaffold-codegen", action="store_true",
        help="走硬编码 scaffold 复用路径(不调 LLM,op_add 基线对比);"
        "默认 False = 真测 LLM 5 节点 codegen(U1 本意)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.list:
        print("5 demo custom CUDA ops:")
        for name, desc in DEMO_OPS.items():
            print(f"  - {name}: {desc[:100]}...")
        return 0

    if args.dry_run:
        print(f"[dry-run] would run {len(DEMO_OPS)} ops against 910B")
        print(f"[dry-run] threshold: pass_rate >= {PASS_THRESHOLD:.0%} → GO")
        for name in DEMO_OPS:
            print(f"  - {name}")
        return 0

    if args.op:
        ops_to_run = [(args.op, DEMO_OPS[args.op])]
    else:
        ops_to_run = list(DEMO_OPS.items())

    results = run_spike(ops_to_run, use_scaffold_codegen=args.scaffold_codegen)
    return report(results)


if __name__ == "__main__":
    sys.exit(main())
