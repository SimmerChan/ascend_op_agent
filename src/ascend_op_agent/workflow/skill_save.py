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

"""SkillSaver - 技能保存与发布

从算子开发结果中提取经验，保存为混合维度Skill:
- 基础模板: {op_type}_{op_name}/
- Bugfix: {op_type}_{op_name}_bugfix/
- 性能优化: {op_type}_{op_name}_performance/
"""

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ascend_op_agent.skills.models import Skill
from ascend_op_agent.skills.storage import SkillStorage
from ascend_op_agent.workflow.models import (
    CodeGenResult,
    CompileResult,
    OpInfo,
    PrecisionReport,
)

logger = logging.getLogger(__name__)


@dataclass
class OpResult:
    """算子开发结果

    封装工作流各阶段的产物，供技能提取使用。
    """
    op_info: OpInfo
    code_gen_result: Optional[CodeGenResult] = None
    compile_result: Optional[CompileResult] = None
    precision_report: Optional[PrecisionReport] = None

    # 额外的编译错误记录（用于提取bugfix）
    compile_errors: list[str] = field(default_factory=list)

    # 性能数据（用于提取性能优化经验）
    performance_data: dict[str, Any] = field(default_factory=dict)

    # 原始代码文件内容（用于提取模板）
    source_files: dict[str, str] = field(default_factory=dict)


class SkillDimension:
    """技能维度"""
    TEMPLATE = "template"
    BUGFIX = "bugfix"
    PERFORMANCE = "performance"


class SkillSaver:
    """技能保存器

    从算子开发结果中提取经验，保存为三种维度的Skill。
    """

    def __init__(
        self,
        storage: Optional[SkillStorage] = None,
        author: Optional[str] = None,
    ):
        """
        Args:
            storage: Skill存储后端
            author: 技能作者
        """
        self.storage = storage or SkillStorage()
        self.author = author or os.getenv("USER", "anonymous")
        self._current_year = datetime.now().year

    def save(
        self,
        op_result: OpResult,
        dimensions: list[str] = None,
        user_confirm: bool = True,
    ) -> dict[str, str]:
        """保存算子开发经验为Skill

        Args:
            op_result: 算子开发结果
            dimensions: 要保存的维度列表，默认保存所有维度
            user_confirm: 是否需要用户确认

        Returns:
            {dimension: path} 保存路径字典
        """
        if dimensions is None:
            dimensions = [
                SkillDimension.TEMPLATE,
                SkillDimension.BUGFIX,
                SkillDimension.PERFORMANCE,
            ]

        if not user_confirm:
            logger.warning("Saving without user confirmation")

        saved_paths = {}

        for dimension in dimensions:
            try:
                skill = self._extract_skill(op_result, dimension)
                if skill:
                    path = self.storage.save_skill(skill, dimension=dimension)
                    saved_paths[dimension] = path
                    logger.info(f"Saved {dimension} skill to {path}")
            except Exception as e:
                logger.error(f"Failed to save {dimension} skill: {e}")

        return saved_paths

    def _extract_skill(
        self,
        op_result: OpResult,
        dimension: str,
    ) -> Optional[Skill]:
        """提取指定维度的Skill

        Args:
            op_result: 算子开发结果
            dimension: 维度

        Returns:
            Skill对象或None
        """
        if dimension == SkillDimension.TEMPLATE:
            return self._extract_template(op_result)
        elif dimension == SkillDimension.BUGFIX:
            return self._extract_bugfix(op_result)
        elif dimension == SkillDimension.PERFORMANCE:
            return self._extract_performance(op_result)
        return None

    def _extract_template(self, op_result: OpResult) -> Skill:
        """提取模板经验

        从代码生成结果中提取可复用的算子模板。
        """
        op_info = op_result.op_info

        # 构建技能名称
        skill_name = f"{op_info.op_type}_{op_info.name}"

        # 构建描述
        description = (
            f"{op_info.op_type}算子开发模板，"
            f"支持{', '.join(op_info.input_dtypes)}数据类型，"
            f"输入shape支持{len(op_info.input_shapes)}种配置"
        )

        # 构建内容
        content_parts = [
            f"## {op_info.name} 算子模板",
            "",
            f"### 基本信息",
            f"- 算子类型: {op_info.op_type}",
            f"- 支持数据类型: {', '.join(op_info.input_dtypes)}",
            f"- 输入维度: {len(op_info.input_shapes)}",
            "",
            f"### 使用方法",
            f"1. 复制模板文件到您的项目",
            f"2. 根据实际需求修改算子逻辑",
            f"3. 调整Tiling参数",
            f"4. 编译验证",
            "",
        ]

        # 添加代码片段（如果有）
        if op_result.source_files:
            content_parts.append("### 代码结构")
            for filename in op_result.source_files:
                content_parts.append(f"\n#### {filename}\n")
                # 截取前100行作为预览
                lines = op_result.source_files[filename].split("\n")[:100]
                content_parts.append("```cpp")
                content_parts.extend(lines)
                content_parts.append("```")
                if len(op_result.source_files[filename].split("\n")) > 100:
                    content_parts.append(f"... ({len(op_result.source_files[filename].split(chr(10))) - 100} more lines)")

        # 添加设计决策
        if op_result.code_gen_result and op_result.code_gen_result.files:
            content_parts.append("")
            content_parts.append("### 生成的文件")
            for f in op_result.code_gen_result.files:
                content_parts.append(f"- `{f.path}` ({f.action})")

        content = "\n".join(content_parts)

        return Skill(
            name=skill_name,
            description=description,
            content=content,
            tags=[op_info.op_type, "template", "ascendc"],
            version="1.0.0",
            author=self.author,
            platforms=["AscendC"],
            prerequisites={
                "cann_version": "23.0+",
                "framework": ["AscendC"],
            },
            metadata={
                "op_name": op_info.name,
                "op_type": op_info.op_type,
                "input_dtypes": op_info.input_dtypes,
                "input_shapes": [str(s) for s in op_info.input_shapes],
            },
        )

    def _extract_bugfix(self, op_result: OpResult) -> Optional[Skill]:
        """提取Bugfix经验

        从编译错误和修复记录中提取经验。
        """
        op_info = op_result.op_info

        # 如果没有编译错误，返回None
        compile_errors = op_result.compile_errors or []
        fixed_errors = []

        # 从compile_result中收集已修复的错误
        if op_result.compile_result:
            fixed_errors = (
                op_result.compile_result.syntax_errors
                + op_result.compile_result.missing_headers
                + op_result.compile_result.type_errors
            )

        all_errors = list(set(compile_errors + fixed_errors))

        if not all_errors:
            logger.info(f"No bugfix experience to extract for {op_info.name}")
            return None

        # 构建技能名称（不带后缀，save_skill会添加）
        skill_name = f"{op_info.op_type}_{op_info.name}"

        # 构建描述
        description = (
            f"{op_info.op_type}算子开发过程中遇到的常见错误及修复方法，"
            f"包含{len(all_errors)}个已记录的问题解决方案"
        )

        # 构建内容
        content_parts = [
            f"## {op_info.name} Bugfix 记录",
            "",
            f"### 概述",
            f"本技能记录了开发 {op_info.name} 算子时遇到的问题及解决方案。",
            "",
            f"### 常见错误及修复",
            "",
        ]

        for i, error in enumerate(all_errors, 1):
            content_parts.append(f"#### 问题 {i}")
            content_parts.append(f"**错误信息**: ```\n{error}\n```")
            content_parts.append("")
            content_parts.append("**可能原因**:")
            content_parts.append("- 语法错误")
            content_parts.append("- 头文件缺失")
            content_parts.append("- 类型不匹配")
            content_parts.append("")
            content_parts.append("**检查方法**:")
            content_parts.append("1. 检查语法是否符合C++17标准")
            content_parts.append("2. 确认所有头文件已正确包含")
            content_parts.append("3. 验证数据类型转换")
            content_parts.append("")

        # 添加编译结果摘要
        if op_result.compile_result:
            content_parts.append("### 编译统计")
            content_parts.append(f"- 编译尝试次数: {op_result.compile_result.fix_attempts + 1}")
            content_parts.append(f"- 最终状态: {'成功' if op_result.compile_result.success else '失败'}")

        content = "\n".join(content_parts)

        return Skill(
            name=skill_name,
            description=description,
            content=content,
            tags=[op_info.op_type, "bugfix", "ascendc", "debug"],
            version="1.0.0",
            author=self.author,
            platforms=["AscendC"],
            prerequisites={
                "cann_version": "23.0+",
                "framework": ["AscendC"],
            },
            metadata={
                "op_name": op_info.name,
                "op_type": op_info.op_type,
                "error_count": len(all_errors),
                "errors": all_errors[:10],  # 最多保存10个
            },
        )

    def _extract_performance(self, op_result: OpResult) -> Optional[Skill]:
        """提取性能优化经验

        从性能评测结果中提取优化经验。
        """
        op_info = op_result.op_info

        # 如果没有性能数据，返回None
        performance_data = op_result.performance_data or {}

        # 从precision_report中提取性能指标
        if op_result.precision_report:
            perf_stats = {
                "avg_abs_err": op_result.precision_report.avg_abs_err,
                "avg_rel_err": op_result.precision_report.avg_rel_err,
                "avg_cos_sim": op_result.precision_report.avg_cos_sim,
                "test_pass_rate": (
                    op_result.precision_report.passed_cases
                    / op_result.precision_report.total_cases
                    if op_result.precision_report.total_cases > 0
                    else 0
                ),
            }
            performance_data.update(perf_stats)

        if not performance_data:
            logger.info(f"No performance experience to extract for {op_info.name}")
            return None

        # 构建技能名称（不带后缀，save_skill会添加）
        skill_name = f"{op_info.op_type}_{op_info.name}"

        # 构建描述
        description = (
            f"{op_info.op_type}算子性能优化经验，"
            f"包含Tiling参数调优和内存布局优化建议"
        )

        # 构建内容
        content_parts = [
            f"## {op_info.name} 性能优化指南",
            "",
            f"### 概述",
            f"本技能记录了 {op_info.name} 算子的性能优化经验。",
            "",
        ]

        # 添加性能指标
        if "test_pass_rate" in performance_data:
            pass_rate = performance_data["test_pass_rate"] * 100
            content_parts.append(f"### 精度指标")
            content_parts.append(f"- 测试通过率: {pass_rate:.1f}%")
            if "avg_abs_err" in performance_data:
                content_parts.append(f"- 平均绝对误差: {performance_data['avg_abs_err']:.6f}")
            if "avg_rel_err" in performance_data:
                content_parts.append(f"- 平均相对误差: {performance_data['avg_rel_err']:.6f}")
            if "avg_cos_sim" in performance_data:
                content_parts.append(f"- 平均余弦相似度: {performance_data['avg_cos_sim']:.6f}")
            content_parts.append("")

        # 添加Tiling建议
        content_parts.append("### Tiling参数建议")
        content_parts.append("""
以下是针对不同输入规模的Tiling参数建议：

| 输入规模 | Tile Shape | Block Dim |
|----------|-----------|----------|
| [32, 32] | [8, 8, 1] | [2, 2, 1] |
| [64, 64] | [16, 16, 1] | [4, 4, 1] |
| [128, 128] | [32, 32, 1] | [8, 8, 1] |
| [256, 256] | [32, 32, 1] | [8, 8, 1] |
""")

        # 添加内存布局建议
        content_parts.append("")
        content_parts.append("### 内存布局优化")
        content_parts.append("""
1. **优先使用ROW_MAJOR布局**：AscendC对行优先布局支持更好
2. **避免频繁的数据转换**：在算子入口处统一内存格式
3. **利用Global Memory缓存**：合理设置Tile大小以提高缓存命中率
""")

        content = "\n".join(content_parts)

        return Skill(
            name=skill_name,
            description=description,
            content=content,
            tags=[op_info.op_type, "performance", "ascendc", "optimization"],
            version="1.0.0",
            author=self.author,
            platforms=["AscendC"],
            prerequisites={
                "cann_version": "23.0+",
                "framework": ["AscendC"],
            },
            metadata={
                "op_name": op_info.name,
                "op_type": op_info.op_type,
                "performance_data": performance_data,
            },
        )

    def publish(
        self,
        skill_name: str,
        remote_repo: str,
        branch: str = "main",
        message: Optional[str] = None,
    ) -> dict[str, Any]:
        """发布技能到远程仓库

        Args:
            skill_name: 技能名称
            remote_repo: 远程仓库URL
            branch: 分支名
            message: 提交消息

        Returns:
            发布结果字典
        """
        skill_path = self.storage.skills_dir / skill_name

        if not skill_path.exists():
            return {
                "success": False,
                "error": f"Skill {skill_name} not found",
            }

        try:
            # 初始化git仓库（如果需要）
            self._init_or_update_git(skill_path, remote_repo, branch)

            # 添加文件
            subprocess.run(
                ["git", "-C", str(skill_path), "add", "-A"],
                check=True,
                capture_output=True,
            )

            # 提交
            commit_message = message or f"feat(skill): add {skill_name} skill"
            subprocess.run(
                ["git", "-C", str(skill_path), "commit", "-m", commit_message],
                check=True,
                capture_output=True,
            )

            # 推送
            subprocess.run(
                ["git", "-C", str(skill_path), "push", "-u", "origin", branch],
                check=True,
                capture_output=True,
            )

            logger.info(f"Published skill {skill_name} to {remote_repo}")

            return {
                "success": True,
                "skill_name": skill_name,
                "remote_repo": remote_repo,
                "branch": branch,
            }

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to publish skill: {e.stderr}")
            return {
                "success": False,
                "error": e.stderr,
            }

    def _init_or_update_git(
        self,
        repo_path: Path,
        remote_repo: str,
        branch: str,
    ) -> None:
        """初始化或更新git仓库"""
        git_dir = repo_path / ".git"

        if not git_dir.exists():
            # 初始化新仓库
            subprocess.run(
                ["git", "init", "-b", branch],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", remote_repo],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
        else:
            # 更新远程仓库URL
            subprocess.run(
                ["git", "remote", "set-url", "origin", remote_repo],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

    def create_pr(
        self,
        skill_name: str,
        remote_repo: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建Pull Request

        Args:
            skill_name: 技能名称
            remote_repo: 远程仓库URL
            title: PR标题
            description: PR描述

        Returns:
            PR创建结果
        """
        # 解析远程仓库URL获取owner和repo
        # 支持 https://gitcode.com/owner/repo 格式
        parts = remote_repo.rstrip("/").split("/")
        if len(parts) >= 2:
            repo_name = parts[-1].replace(".git", "")
            owner = parts[-2]
        else:
            return {
                "success": False,
                "error": "Invalid remote repo URL",
            }

        pr_title = title or f"feat(skill): add {skill_name} skill"
        pr_body = description or f"""## Summary
- Add {skill_name} skill to the repository

## Testing
- Skill has been tested locally

## Checklist
- [x] SKILL.md is properly formatted
- [x] Content is accurate and helpful
- [x] Tags are appropriate
"""

        try:
            # 使用gh命令创建PR
            result = subprocess.run(
                [
                    "gh", "pr", "create",
                    "--repo", f"{owner}/{repo_name}",
                    "--title", pr_title,
                    "--body", pr_body,
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            pr_url = result.stdout.strip()

            logger.info(f"Created PR for skill {skill_name}: {pr_url}")

            return {
                "success": True,
                "pr_url": pr_url,
                "skill_name": skill_name,
            }

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create PR: {e.stderr}")
            return {
                "success": False,
                "error": e.stderr,
            }
