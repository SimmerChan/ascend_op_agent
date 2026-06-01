"""NPU hardware introspection tools.

Provides npu_smi, msop, and cann_compile functions.
"""
import os
import subprocess
from typing import Optional


def npu_smi(device_id: Optional[str] = None) -> str:
    """Query NPU device information via npu-smi CLI.

    Args:
        device_id: Optional device ID to query specific device

    Returns:
        NPU device info or error message
    """
    try:
        args = ['npu-smi', 'info']

        if device_id is not None:
            args.extend(['-d', str(device_id)])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            return result.stdout if result.stdout else "无 NPU 设备信息"
        else:
            # Check if npu-smi is not installed
            if "not found" in result.stderr.lower() or result.returncode == 127:
                return "错误: npu-smi 未安装或不在 PATH 中。请安装 CANN 工具包。"
            return f"错误: {result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: npu-smi 查询超时"

    except FileNotFoundError:
        return "错误: npu-smi 未安装。请安装 CANN 工具包。"

    except Exception as e:
        return f"错误: {str(e)}"


def msop(operator_path: str, analyze: bool = True) -> str:
    """Run CANN msop operator analysis tool.

    Args:
        operator_path: Path to operator file or directory
        analyze: If True, run analysis; otherwise just validate

    Returns:
        Analysis output or error message
    """
    if not os.path.exists(operator_path):
        return f"错误: 路径不存在: {operator_path}"

    try:
        args = ['msop']

        if analyze:
            args.append('-a')

        args.append(operator_path)

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            return result.stdout if result.stdout else "分析完成（无输出）"
        else:
            if "not found" in result.stderr.lower() or result.returncode == 127:
                return "错误: msop 未安装或不在 PATH 中。请安装 CANN 工具包。"
            return f"错误:\n{result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: msop 执行超时"

    except FileNotFoundError:
        return "错误: msop 未安装。请安装 CANN 工具包。"

    except Exception as e:
        return f"错误: {str(e)}"


def cann_compile(operator_path: str, target: str = "npu") -> str:
    """Run CANN compilation for an operator.

    Args:
        operator_path: Path to operator source
        target: Compilation target (default: npu)

    Returns:
        Compilation result or error message
    """
    if not os.path.exists(operator_path):
        return f"错误: 路径不存在: {operator_path}"

    # Check if CANN is activated
    cann_home = os.environ.get('ASCEND_OPP_PATH') or os.environ.get('CANN_HOME')
    if not cann_home:
        return "警告: CANN 环境未配置（ASCEND_OPP_PATH 或 CANN_HOME 未设置）。请先 source cann脚本。"

    try:
        args = ['cann_compile', '-target', target, operator_path]

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes for compilation
        )

        if result.returncode == 0:
            return result.stdout if result.stdout else "编译成功"
        else:
            return f"编译错误:\n{result.stderr}"

    except subprocess.TimeoutExpired:
        return "错误: 编译超时（5分钟）"

    except FileNotFoundError:
        return "错误: cann_compile 未安装。请安装 CANN 工具包。"

    except Exception as e:
        return f"错误: {str(e)}"


# Schema for npu_smi
NPU_SMI_SCHEMA = {
    "name": "npu_smi",
    "description": "查询昇腾 NPU 设备信息（设备 ID、内存、利用率等）。",
    "parameters": {
        "type": "object",
        "properties": {
            "device_id": {
                "type": "string",
                "description": "设备 ID（可选）"
            }
        },
        "required": []
    }
}

# Schema for msop
MSOP_SCHEMA = {
    "name": "msop",
    "description": "运行 CANN msop 算子分析工具，分析算子实现。",
    "parameters": {
        "type": "object",
        "properties": {
            "operator_path": {
                "type": "string",
                "description": "算子文件或目录路径"
            },
            "analyze": {
                "type": "boolean",
                "description": "是否运行分析模式",
                "default": True
            }
        },
        "required": ["operator_path"]
    }
}

# Schema for cann_compile
CANN_COMPILE_SCHEMA = {
    "name": "cann_compile",
    "description": "运行 CANN 编译命令编译算子。",
    "parameters": {
        "type": "object",
        "properties": {
            "operator_path": {
                "type": "string",
                "description": "算子源文件路径"
            },
            "target": {
                "type": "string",
                "description": "编译目标平台",
                "default": "npu"
            }
        },
        "required": ["operator_path"]
    }
}


def register(registry):
    """Register all NPU tools with the registry."""
    registry.register(
        name="npu_smi",
        description=NPU_SMI_SCHEMA["description"],
        func=lambda **kw: npu_smi(
            device_id=kw.get("device_id"),
        ),
        parameters=NPU_SMI_SCHEMA,
        toolset="hardware",
        emoji="🔧",
        max_result_size_chars=50_000
    )

    registry.register(
        name="msop",
        description=MSOP_SCHEMA["description"],
        func=lambda **kw: msop(
            operator_path=kw.get("operator_path"),
            analyze=kw.get("analyze", True),
        ),
        parameters=MSOP_SCHEMA,
        toolset="hardware",
        emoji="🔍",
        max_result_size_chars=50_000
    )

    registry.register(
        name="cann_compile",
        description=CANN_COMPILE_SCHEMA["description"],
        func=lambda **kw: cann_compile(
            operator_path=kw.get("operator_path"),
            target=kw.get("target", "npu"),
        ),
        parameters=CANN_COMPILE_SCHEMA,
        toolset="hardware",
        emoji="⚡",
        max_result_size_chars=50_000
    )
