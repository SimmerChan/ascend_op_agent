"""U13 SSH 远程硬件冒烟测试(910B)。

在本地开发机干净跳过;当 ``NPU_HOST`` / ``NPU_USER`` 环境变量设置(或
``~/.ascend_op_agent/config.yaml`` 配了 remote)时自动跑通。

验证链路:本地 → SSHEnvironment → 910B 远程 cann_compile。

环境变量(任选其一):

- 直接 env:``NPU_HOST`` / ``NPU_USER`` / ``NPU_KEY_PATH`` 或 ``NPU_PASSWORD``
  / ``NPU_CANN_SETUP``(远程 source CANN 的脚本路径,默认 set_env.sh)
- 或 config:``~/.ascend_op_agent/config.yaml`` 的 ``remote:`` 段

fixture operator 路径:``NPU_OPERATOR_PATH``(910B 上一个可编译的算子目录,
缺省时只跑 env/cann_compile 探测,不跑真实编译)。

运行::

    NPU_HOST=10.x.x.x NPU_USER=HwHiAiUser NPU_KEY_PATH=~/.ssh/id_rsa \
    NPU_OPERATOR_PATH=/home/HwHiAiUser/operators/add \
    pytest -m hardware tests/integration/test_ssh_compile_smoke.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ascend_op_agent.config import load_config


def _ssh_env_or_skip():
    """返回 (SSHEnvironment, cann_setup) 或 skip。

    优先级:env > config.remote。
    """
    host = os.environ.get("NPU_HOST")
    user = os.environ.get("NPU_USER")
    key_path = os.environ.get("NPU_KEY_PATH") or None
    password = os.environ.get("NPU_PASSWORD") or None
    cann_setup = os.environ.get(
        "NPU_CANN_SETUP",
        "/usr/local/Ascend/ascend-toolkit/set_env.sh",
    )

    # 回退:从 config.yaml 读
    if not host or not user:
        try:
            cfg = load_config()
            if cfg.remote:
                host = host or cfg.remote.host
                user = user or cfg.remote.user
                key_path = key_path or cfg.remote.key_path
                password = password or cfg.remote.password
        except Exception:
            pass

    if not host or not user:
        pytest.skip(
            "NPU_HOST / NPU_USER 未设置且 config.yaml 未配 remote —— SSH 冒烟跳过"
        )

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
    return ssh_env, cann_setup


@pytest.mark.hardware
def test_ssh_connect_and_detect_cann() -> None:
    """SSH 能连上 910B,且 CANN env 可被 remote_env_setup 加载。"""
    from ascend_op_agent.orchestrator import NpuExecutor

    ssh_env, cann_setup = _ssh_env_or_skip()
    remote_setup = f"source {cann_setup} && "

    try:
        ssh_env.execute("echo connected", timeout=15)
    except Exception as e:
        pytest.fail(f"SSH 连接 910B 失败: {e}")

    executor = NpuExecutor(ssh_env=ssh_env, remote_env_setup=remote_setup)
    assert executor.is_remote_cann_available(), (
        f"远程 ASCEND_OPP_PATH 探测失败 —— 检查 {cann_setup} 是否存在且可 source"
    )


@pytest.mark.hardware
def test_ssh_cann_compile_available() -> None:
    """910B 上 cann_compile 二进制可用。"""
    ssh_env, cann_setup = _ssh_env_or_skip()
    remote_setup = f"source {cann_setup} && "

    result = ssh_env.execute(f"{remote_setup}which cann_compile", timeout=15)
    assert result.return_code == 0, (
        f"cann_compile 不在 PATH —— stdout={result.stdout} stderr={result.stderr}"
    )
    cann_path = result.stdout.strip()
    assert cann_path, "which cann_compile 返回空"


@pytest.mark.hardware
def test_ssh_npu_arch_detected() -> None:
    """探测 910B 芯片架构(决定 cann_compile -target)。"""
    ssh_env, cann_setup = _ssh_env_or_skip()
    remote_setup = f"source {cann_setup} && "

    result = ssh_env.execute(f"{remote_setup}npu-smi info", timeout=15)
    # npu-smi info 输出含芯片型号;打印到 stdout 便于人工确认
    print("\n=== npu-smi info ===\n" + result.stdout)
    assert result.return_code == 0, f"npu-smi info 失败: {result.stderr}"


@pytest.mark.hardware
def test_ssh_compile_real_operator() -> None:
    """端到端:对 910B 上的真实算子跑 NpuExecutor.compile。

    需设 ``NPU_OPERATOR_PATH``(910B 上的算子目录路径);未设则跳过。
    """
    from ascend_op_agent.orchestrator import NpuExecutor

    operator_path = os.environ.get("NPU_OPERATOR_PATH")
    if not operator_path:
        pytest.skip("NPU_OPERATOR_PATH 未设置 —— 跳过真实算子编译")

    ssh_env, cann_setup = _ssh_env_or_skip()
    remote_setup = f"source {cann_setup} && "

    executor = NpuExecutor(ssh_env=ssh_env, remote_env_setup=remote_setup)
    outcome = executor.compile(operator_path, target="npu")

    # 真实编译可能因 fixture 不匹配 910B arch 而失败 —— 打印详情便于调试
    print(f"\n=== compile outcome ===\n{outcome}")
    assert outcome.success, (
        f"编译失败 return_code={outcome.return_code}: {outcome.stderr}"
    )
