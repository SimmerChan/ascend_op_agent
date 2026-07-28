"""U13 SSH 远程硬件冒烟测试(910B)。

在本地开发机干净跳过;当 ``NPU_HOST`` / ``NPU_USER`` 环境变量设置(或
``~/.ascend_op_agent/config.yaml`` 配了 remote)时自动跑通。

验证链路:本地 → SSHEnvironment → 910B 远程 cann_compile。

环境变量(任选其一):

- 直接 env:``NPU_HOST`` / ``NPU_USER`` / ``NPU_KEY_PATH`` 或 ``NPU_PASSWORD``
  / ``NPU_CANN_SETUP``(远程 source CANN 的脚本路径,默认 set_env.sh)
  / ``NPU_CONTAINER``(远程开发容器名,如 ops_pt;非空时命令包进 docker exec)
- 或 config:``~/.ascend_op_agent/config.yaml`` 的 ``remote:`` 段(container_name)

fixture operator 路径:``NPU_OPERATOR_PATH``(容器内一个可编译的算子工程目录,
含 op_host/op_kernel/CMakeLists;缺省时只跑 env/msopgen 探测,不跑真实编译)。

运行(910B @ 192.168.9.105,容器 ops_pt)::

    NPU_HOST=192.168.9.105 NPU_USER=root NPU_CONTAINER=ops_pt \
    NPU_OPERATOR_PATH=/home/hsl/ops_agent/operators/add \
    pytest -m hardware tests/integration/test_ssh_compile_smoke.py -v -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ascend_op_agent.config import load_config


def _ssh_env_or_skip():
    """返回 (SSHEnvironment, cann_setup, container_name) 或 skip。

    优先级:env > config.remote。
    container_name 非空时 NpuExecutor 会把远程命令包进 docker exec。
    """
    host = os.environ.get("NPU_HOST")
    user = os.environ.get("NPU_USER")
    key_path = os.environ.get("NPU_KEY_PATH") or None
    password = os.environ.get("NPU_PASSWORD") or None
    cann_setup = os.environ.get(
        "NPU_CANN_SETUP",
        "/usr/local/Ascend/ascend-toolkit/set_env.sh",
    )
    container = os.environ.get("NPU_CONTAINER") or None

    # 回退:从 config.yaml 读
    if not host or not user:
        try:
            cfg = load_config()
            if cfg.remote:
                host = host or cfg.remote.host
                user = user or cfg.remote.user
                key_path = key_path or cfg.remote.key_path
                password = password or cfg.remote.password
                container = container or cfg.remote.container_name or None
        except Exception:
            pass

    if not host or not user:
        pytest.skip("NPU_HOST / NPU_USER 未设置且 config.yaml 未配 remote —— SSH 冒烟跳过")

    # 延迟 import 避免无 paramiko 环境报错
    from ascend_op_agent.ssh.manager import SSHEnvironment

    ssh_env = SSHEnvironment(
        host=host,
        user=user,
        port=int(os.environ.get("NPU_PORT", "22")),
        key_path=key_path,
        password=password,
        timeout=30,
    )
    return ssh_env, cann_setup, container


@pytest.mark.hardware
def test_ssh_connect_and_detect_cann() -> None:
    """SSH 能连上 910B(可选进容器),且 CANN env 可被 remote_env_setup 加载。"""
    from ascend_op_agent.orchestrator import NpuExecutor

    ssh_env, cann_setup, container = _ssh_env_or_skip()
    remote_setup = f"source {cann_setup} && "

    try:
        ssh_env.execute("echo connected", timeout=15)
    except Exception as e:
        pytest.fail(f"SSH 连接 910B 失败: {e}")

    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=remote_setup,
        container_name=container or "",
    )
    assert (
        executor.is_remote_cann_available()
    ), f"远程 ASCEND_OPP_PATH 探测失败 —— 检查 {cann_setup} 是否存在且可 source" + (
        f"(容器 {container})" if container else ""
    )


@pytest.mark.hardware
def test_ssh_msopgen_available() -> None:
    """910B 上 msopgen 二进制可用(CANN 9.1.0 标准编译工具)。"""
    from ascend_op_agent.orchestrator import NpuExecutor

    ssh_env, cann_setup, container = _ssh_env_or_skip()
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=f"source {cann_setup} && ",
        container_name=container or "",
    )
    cmd = executor._wrap_remote_cmd("which msopgen")
    result = ssh_env.execute(cmd, timeout=15)
    assert (
        result.return_code == 0
    ), f"msopgen 不在 PATH —— stdout={result.stdout} stderr={result.stderr}"
    assert result.stdout.strip(), "which msopgen 返回空"


@pytest.mark.hardware
def test_ssh_npu_arch_detected() -> None:
    """探测 910B 芯片架构(910B3 → soc_version=Ascend910B3,决定工程内 arch 配置)。"""
    from ascend_op_agent.orchestrator import NpuExecutor

    ssh_env, cann_setup, container = _ssh_env_or_skip()
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=f"source {cann_setup} && ",
        container_name=container or "",
    )
    result = ssh_env.execute(executor._wrap_remote_cmd("npu-smi info"), timeout=15)
    print("\n=== npu-smi info ===\n" + result.stdout)
    assert result.return_code == 0, f"npu-smi info 失败: {result.stderr}"


@pytest.mark.hardware
def test_ssh_compile_real_operator() -> None:
    """端到端:对 910B 上的真实算子工程跑 NpuExecutor.compile(msopgen)。

    需设 ``NPU_OPERATOR_PATH``(容器内的算子工程目录,含 op_host/op_kernel);
    未设则跳过。
    """
    from ascend_op_agent.orchestrator import NpuExecutor

    operator_path = os.environ.get("NPU_OPERATOR_PATH")
    if not operator_path:
        pytest.skip("NPU_OPERATOR_PATH 未设置 —— 跳过真实算子编译")

    ssh_env, cann_setup, container = _ssh_env_or_skip()
    executor = NpuExecutor(
        ssh_env=ssh_env,
        remote_env_setup=f"source {cann_setup} && ",
        container_name=container or "",
    )
    outcome = executor.compile(operator_path, soc_version="ascend910b")

    print(f"\n=== compile outcome ===\n{outcome}")
    assert outcome.success, f"编译失败 return_code={outcome.return_code}: {outcome.stderr}"
