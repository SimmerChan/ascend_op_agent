"""U1:e2e_real_op make_operator_path_resolver 保留子目录结构单测(方向 B 前置)。

原 bug:Path(first).parent + Path.name 扁平化,把 op_kernel/arch22/x.cpp 落到
op_add/x.cpp,破坏 CMakeLists 子目录引用。方向 B codegen 产子目录结构,resolver
必须 tar 整个 operator_dir 保留子目录。

覆盖:
- files 含子目录路径(op_kernel/arch22/x.cpp + op_host/y.cpp)-> 落盘保留子目录 + tar operator_dir
- 不扁平化:op_kernel/arch22/kernel.cpp 不落到根
- 兼容旧扁平路径:f.path 不在 operator_dir 下 -> basename 落根
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# 动态加载 scripts/e2e_real_op.py(非 package,同 test_e2e_real_op_stress.py 模式)
_E2E_PATH = Path(__file__).parent.parent.parent.parent / "scripts" / "e2e_real_op.py"
_spec = importlib.util.spec_from_file_location("e2e_real_op_subdir", _E2E_PATH)
assert _spec and _spec.loader, "load e2e_real_op spec failed"
e2e = importlib.util.module_from_spec(_spec)
sys.modules["e2e_real_op_subdir"] = e2e
_spec.loader.exec_module(e2e)


def _make_resolver(operator_dir, monkeypatch, captured):
    def fake_rsync(local, remote):
        captured["local"] = local
        captured["remote"] = remote

    monkeypatch.setattr(e2e, "_rsync_to_npu", fake_rsync)
    return e2e.make_operator_path_resolver(
        remote_workdir="/remote/wd", operator_dir=str(operator_dir)
    )


# ---- 子目录保留 + tar operator_dir ----


def test_resolver_preserves_subdir_structure(tmp_path, monkeypatch):
    operator_dir = tmp_path / "op_add"
    captured = {}
    resolver = _make_resolver(operator_dir, monkeypatch, captured)
    state = {
        "code_result": {
            "files": [
                {"path": str(operator_dir / "op_kernel/arch22/x.cpp"), "content": "kernel"},
                {"path": str(operator_dir / "op_host/y.cpp"), "content": "host"},
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "cmake"},
                {"path": str(operator_dir / "build.sh"), "content": "bash"},
            ]
        }
    }
    remote = resolver(state)
    # 子目录文件保留(非扁平到根)
    assert (operator_dir / "op_kernel/arch22/x.cpp").read_text() == "kernel"
    assert (operator_dir / "op_host/y.cpp").read_text() == "host"
    assert (operator_dir / "CMakeLists.txt").read_text() == "cmake"
    assert (operator_dir / "build.sh").read_text() == "bash"
    # tar 整个 operator_dir(非首文件父目录)
    assert captured["local"] == str(operator_dir)
    assert captured["remote"] == "/remote/wd/op_add"
    assert remote == "/remote/wd/op_add"


# ---- 不扁平化:子目录文件不落到根 ----


def test_resolver_does_not_flatten_subdir_to_root(tmp_path, monkeypatch):
    """原 bug:Path.name 把 op_kernel/arch22/kernel.cpp 落到 op_add/kernel.cpp。验证子目录保留。"""
    operator_dir = tmp_path / "op_add"
    captured = {}
    resolver = _make_resolver(operator_dir, monkeypatch, captured)
    state = {
        "code_result": {
            "files": [
                {"path": str(operator_dir / "op_kernel/arch22/kernel.cpp"), "content": "K"},
                {"path": str(operator_dir / "op_kernel/CMakeLists.txt"), "content": "KC"},
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "ROOT_CMAKE"},
                {"path": str(operator_dir / "build.sh"), "content": "BASH"},
            ]
        }
    }
    resolver(state)
    # 子目录文件不在根
    assert not (operator_dir / "kernel.cpp").exists()
    assert (operator_dir / "op_kernel/arch22/kernel.cpp").exists()
    assert (operator_dir / "op_kernel/CMakeLists.txt").exists()
    # 根 CMakeLists 保留(不被子目录覆盖)
    assert (operator_dir / "CMakeLists.txt").read_text() == "ROOT_CMAKE"


# ---- 兼容旧扁平路径(f.path 不在 operator_dir 下)----


def test_resolver_fallback_basename_for_external_path(tmp_path, monkeypatch):
    """f.path 不在 operator_dir 下(旧扁平 /tmp/op/x.cpp)-> basename 落 operator_dir 根。"""
    operator_dir = tmp_path / "op_add"
    captured = {}
    resolver = _make_resolver(operator_dir, monkeypatch, captured)
    state = {
        "code_result": {
            "files": [
                {"path": "/tmp/elsewhere/op_kernel.cpp", "content": "K"},
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "C"},
                {"path": str(operator_dir / "build.sh"), "content": "B"},
            ]
        }
    }
    resolver(state)
    # 外部路径用 basename 落 operator_dir 根(兼容)
    assert (operator_dir / "op_kernel.cpp").read_text() == "K"
    assert (operator_dir / "CMakeLists.txt").read_text() == "C"
    assert captured["local"] == str(operator_dir)


# ---- 首文件在子目录时 local_dir 仍是 op 根(原 bug 核心)----


def test_resolver_local_dir_is_op_root_when_first_file_in_subdir(tmp_path, monkeypatch):
    """原 bug:files[0] 在 op_kernel/arch22/ 时 local_dir=该子目录,tar 只 tar 一个子目录。
    方向 B:local_dir=operator_dir(op 根),tar 整个工程。"""
    operator_dir = tmp_path / "op_add"
    captured = {}
    resolver = _make_resolver(operator_dir, monkeypatch, captured)
    # files[0] 在子目录(模拟方向 B codegen 产出顺序)
    state = {
        "code_result": {
            "files": [
                {"path": str(operator_dir / "op_kernel/arch22/entry.cpp"), "content": "E"},
                {"path": str(operator_dir / "op_host/def.cpp"), "content": "D"},
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "C"},
                {"path": str(operator_dir / "build.sh"), "content": "B"},
            ]
        }
    }
    resolver(state)
    # tar operator_dir(非 op_kernel/arch22/)
    assert captured["local"] == str(operator_dir)
    assert captured["remote"] == "/remote/wd/op_add"
    # 所有子目录文件落盘
    assert (operator_dir / "op_kernel/arch22/entry.cpp").exists()
    assert (operator_dir / "op_host/def.cpp").exists()


# ---- U6:should_sync=False(precision 不擦 build/)----


def test_resolver_should_sync_false_skips_rsync(tmp_path, monkeypatch):
    """should_sync=False(precision 用)→ 不调 _rsync_to_npu(不擦远程 build/),
    但仍落盘本地 + 返回远程路径。"""
    operator_dir = tmp_path / "op_add"
    captured = {}
    monkeypatch.setattr(
        e2e, "_rsync_to_npu", lambda local, remote: captured.update(local=local, remote=remote)
    )
    resolver = e2e.make_operator_path_resolver(
        remote_workdir="/remote/wd", operator_dir=str(operator_dir), should_sync=False
    )
    state = {
        "code_result": {
            "files": [
                {"path": str(operator_dir / "op_host/def.cpp"), "content": "D"},
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "C"},
                {"path": str(operator_dir / "build.sh"), "content": "B"},
            ]
        }
    }
    remote = resolver(state)
    # 不 sync(captured 空)—— 保护远程已编译的 build/
    assert captured == {}
    # 路径照常返回
    assert remote == "/remote/wd/op_add"
    # 本地落盘仍发生(precision 无害副作用)
    assert (operator_dir / "op_host/def.cpp").read_text() == "D"


def test_resolver_should_sync_true_default_calls_rsync(tmp_path, monkeypatch):
    """默认 should_sync=True(compile_fix_loop 用)→ 调 _rsync_to_npu(回归保护)。"""
    operator_dir = tmp_path / "op_add"
    captured = {}
    monkeypatch.setattr(
        e2e, "_rsync_to_npu", lambda local, remote: captured.update(local=local, remote=remote)
    )
    resolver = e2e.make_operator_path_resolver(
        remote_workdir="/remote/wd", operator_dir=str(operator_dir)
    )
    state = {
        "code_result": {
            "files": [
                {"path": str(operator_dir / "CMakeLists.txt"), "content": "C"},
                {"path": str(operator_dir / "build.sh"), "content": "B"},
            ]
        }
    }
    resolver(state)
    assert captured == {"local": str(operator_dir), "remote": "/remote/wd/op_add"}
