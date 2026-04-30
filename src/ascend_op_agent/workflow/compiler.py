# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Compiler - 编译验证和自动修复

支持确定性修复：
- 语法错误
- 拼写错误
- 缺失头文件
- 类型不匹配
"""

import logging
import re
import subprocess
from typing import Optional

from ascend_op_agent.workflow.models import CompileResult

logger = logging.getLogger(__name__)


# 常见头文件映射
COMMON_HEADERS = {
    "vector": ["<vector>", "#include <vector>"],
    "string": ["<string>", "#include <string>"],
    "map": ["<map>", "#include <map>"],
    "iostream": ["<iostream>", "#include <iostream>"],
    "memory": ["<memory>", "#include <memory>"],
    "functional": ["<functional>", "#include <functional>"],
    "tensor": ["acl/acl_base.h", "#include \"acl/acl_base.h\""],
    "kernel": ["kernel_operator.h", "#include \"kernel_operator.h\""],
    "kernel_base": ["kernel_base.h", "#include \"kernel_base.h\""],
}


class Compiler:
    """AscendC算子编译器

    支持编译和确定性错误修复。
    """

    def __init__(
        self,
        workspace: str,
        build_dir: str = "build",
    ):
        """
        Args:
            workspace: 工作空间路径
            build_dir: 构建目录
        """
        self.workspace = workspace
        self.build_dir = build_dir

    def compile(
        self,
        source_files: list[str],
        cmake_config: Optional[dict] = None,
    ) -> CompileResult:
        """编译源文件

        Args:
            source_files: 源文件列表
            cmake_config: CMake配置

        Returns:
            CompileResult编译结果
        """
        # 构建编译命令
        cmake_cmd = ["cmake", "-B", self.build_dir, "-S", self.workspace]
        make_cmd = ["cmake", "--build", self.build_dir]

        try:
            # CMake配置
            cmake_result = subprocess.run(
                cmake_cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if cmake_result.returncode != 0:
                return CompileResult(
                    success=False,
                    command=" ".join(cmake_cmd),
                    stdout=cmake_result.stdout,
                    stderr=cmake_result.stderr,
                    return_code=cmake_result.returncode,
                )

            # 编译
            make_result = subprocess.run(
                make_cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            success = make_result.returncode == 0

            return CompileResult(
                success=success,
                command=" ".join(make_cmd),
                stdout=make_result.stdout,
                stderr=make_result.stderr,
                return_code=make_result.returncode,
            )

        except subprocess.TimeoutExpired:
            return CompileResult(
                success=False,
                command=" ".join(make_cmd),
                stdout="",
                stderr="Compilation timeout",
                return_code=-1,
            )
        except Exception as e:
            logger.error(f"Compilation failed: {e}")
            return CompileResult(
                success=False,
                command=" ".join(make_cmd),
                stdout="",
                stderr=str(e),
                return_code=-1,
            )

    def analyze_errors(self, result: CompileResult) -> CompileResult:
        """分析编译错误并分类

        Args:
            result: 编译结果

        Returns:
            更新后的CompileResult（含错误分类）
        """
        stderr = result.stderr

        # 语法错误
        syntax_patterns = [
            r"error: expected.*",
            r"error:.*'{.*}'",
            r"error:.*';'",
            r"error:.*'::'",
        ]
        for pattern in syntax_patterns:
            matches = re.findall(pattern, stderr, re.MULTILINE)
            result.syntax_errors.extend(matches)

        # 缺失头文件
        header_patterns = [
            r"fatal error: (\w+): No such file or directory",
            r"error:.*'([^']+)' file not found",
        ]
        for pattern in header_patterns:
            matches = re.findall(pattern, stderr)
            result.missing_headers.extend(matches)

        # 类型错误
        type_patterns = [
            r"error:.*cannot convert.*to.*",
            r"error:.*invalid conversion.*",
            r"error:.*incompatible.*",
        ]
        for pattern in type_patterns:
            matches = re.findall(pattern, stderr, re.MULTILINE)
            result.type_errors.extend(matches)

        # 其他错误
        all_errors = set(result.syntax_errors + result.missing_headers + result.type_errors)
        other_errors = re.findall(r"error:.*", stderr, re.MULTILINE)
        result.other_errors = [e for e in other_errors if e not in all_errors]

        return result

    def fix_errors(self, result: CompileResult) -> list[str]:
        """尝试修复编译错误

        Args:
            result: 编译结果

        Returns:
            修复描述列表
        """
        fixes = []

        # 修复缺失头文件
        for header in result.missing_headers:
            if header in COMMON_HEADERS:
                fixes.append(f"Should add: {COMMON_HEADERS[header][1]}")
            else:
                fixes.append(f"Missing header: {header}")

        # 修复类型错误（简单提示）
        for type_err in result.type_errors:
            fixes.append(f"Type error needs review: {type_err[:50]}")

        return fixes


class CodeFixer:
    """代码修复器

    确定性修复策略：
    - 语法错误：直接修复
    - 拼写错误：使用常见拼写修正
    - 缺失头文件：自动添加
    - 类型不匹配：需要用户确认
    """

    # 常见拼写错误修正
    SPELLING_CORRECTIONS = {
        "lenght": "length",
        "widht": "width",
        "hight": "height",
        "acheive": "achieve",
        "occured": "occurred",
        "recieve": "receive",
        "thier": "their",
        "teh": "the",
        " programmes ": "programs",
        " programme ": "program",
    }

    @classmethod
    def fix_spelling(cls, content: str) -> str:
        """修复拼写错误

        Args:
            content: 源代码内容

        Returns:
            修复后的内容
        """
        fixed = content
        for wrong, correct in cls.SPELLING_CORRECTIONS.items():
            fixed = fixed.replace(wrong, correct)
        return fixed

    @classmethod
    def add_header(cls, content: str, header: str) -> str:
        """添加头文件

        Args:
            content: 源代码内容
            header: 头文件（如 "vector" 或完整include语句）

        Returns:
            修复后的内容
        """
        if not header.startswith("#include"):
            include_stmt = f'#include "{header}"'
        else:
            include_stmt = header

        # 检查是否已包含
        if include_stmt in content:
            return content

        # 添加到文件开头（在license之后，如果存在）
        lines = content.split("\n")
        insert_idx = 0

        # 找到最后一个license comment或include之后的位置
        for i, line in enumerate(lines):
            if line.startswith("//") or line.startswith("/*"):
                insert_idx = i + 1
            elif include_stmt in line:
                return content

        lines.insert(insert_idx, include_stmt)
        return "\n".join(lines)

    @classmethod
    def fix_syntax_error(cls, content: str, error_line: str) -> str:
        """修复语法错误

        Args:
            content: 源代码内容
            error_line: 错误行

        Returns:
            修复后的内容
        """
        # 常见的简单语法错误修复
        lines = content.split("\n")

        # 查找错误行
        for i, line in enumerate(lines):
            if error_line.strip() in line or line.strip() == error_line.strip():
                # 修复缺失分号
                if not line.strip().endswith((";", "{", "}", "//")) and not line.strip().startswith("//"):
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("//"):
                        # 注释行前需要分号
                        lines[i] = line.rstrip() + ";"
                        return "\n".join(lines)

        return content
