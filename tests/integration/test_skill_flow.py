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

"""Skill流程集成测试

测试技能保存、检索和应用的完整流程。
"""

import os
import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.skills.models import Skill, SkillInfo
from ascend_op_agent.skills.storage import SkillStorage
from ascend_op_agent.skills.repository import SkillRepository
from ascend_op_agent.workflow.skill_save import SkillSaver, OpResult, SkillDimension
from ascend_op_agent.workflow.models import (
    CodeGenResult,
    CompileResult,
    OpInfo,
    FileChange,
)


class TestSkillSaveAndRetrieve:
    """技能保存和检索流程测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)
        self.saver = SkillSaver(storage=self.storage)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_skill_flow(self):
        """测试完整的技能流程：保存 -> 加载"""
        # 1. 创建OpResult
        op_info = OpInfo(
            name="test_add",
            description="Test addition operator",
            op_type="elementwise",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32"],
        )
        code_gen = CodeGenResult(
            success=True,
            files=[FileChange(path="kernel.cpp", action="create", content="// test")],
            generated_files=["kernel.cpp"],
        )
        compile_result = CompileResult(
            success=True,
            command="make",
        )

        op_result = OpResult(
            op_info=op_info,
            code_gen_result=code_gen,
            compile_result=compile_result,
        )

        # 2. 保存技能
        saved_paths = self.saver.save(op_result, user_confirm=False)

        assert SkillDimension.TEMPLATE in saved_paths
        template_path = saved_paths[SkillDimension.TEMPLATE]
        assert Path(template_path).exists()

        # 3. 验证技能被存储
        skills = self.storage.list_skills()
        assert "elementwise_test_add" in skills

        # 4. 加载技能
        skill = self.storage.load_skill("elementwise_test_add")
        assert skill is not None
        assert skill.name == "elementwise_test_add"

    def test_skill_with_all_dimensions(self):
        """测试保存所有维度的技能"""
        op_info = OpInfo(
            name="matmul_op",
            description="Matrix multiplication operator",
            op_type="matmul",
            input_shapes=[[512, 256], [256, 128]],
            input_dtypes=["float16", "float16"],
        )
        code_gen = CodeGenResult(
            success=True,
            files=[FileChange(path="kernel.cpp", action="create", content="// matmul")],
            generated_files=["kernel.cpp"],
        )
        compile_result = CompileResult(
            success=True,
            command="make",
            syntax_errors=["error: missing return"],
        )

        op_result = OpResult(
            op_info=op_info,
            code_gen_result=code_gen,
            compile_result=compile_result,
            compile_errors=["error: missing return"],
        )

        # 保存所有维度
        saved_paths = self.saver.save(op_result, user_confirm=False)

        # 验证 template 和 bugfix 都被保存
        # performance 可能没有（取决于是否有 performance_data）
        assert SkillDimension.TEMPLATE in saved_paths
        assert SkillDimension.BUGFIX in saved_paths

    def test_skill_without_bugfix(self):
        """测试没有bugfix时的技能保存"""
        op_info = OpInfo(
            name="simple_op",
            description="Simple operator",
            op_type="elementwise",
        )
        code_gen = CodeGenResult(success=True, files=[], generated_files=[])

        op_result = OpResult(
            op_info=op_info,
            code_gen_result=code_gen,
        )

        saved_paths = self.saver.save(op_result, user_confirm=False)

        # 只有template，没有bugfix
        assert SkillDimension.TEMPLATE in saved_paths
        assert SkillDimension.BUGFIX not in saved_paths


class TestSkillRepository:
    """Skill仓库测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.repo = SkillRepository(local_skills_dir=self.temp_dir)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_local_skill_discovery(self):
        """测试本地技能发现"""
        # 创建测试技能
        skill_dir = Path(self.temp_dir) / "test_skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: test_skill
description: A test skill
tags: [test]
---

# Test Skill
This is a test skill.
""")

        # 发现技能
        skills = self.repo.list_local_skills()

        assert len(skills) >= 1
        skill_names = [s.name for s in skills]
        assert "test_skill" in skill_names


class TestSkillWorkflowIntegration:
    """技能与工作流集成测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)
        self.saver = SkillSaver(storage=self.storage)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_workflow_result_to_skill(self):
        """测试将工作流结果转换为技能"""
        op_info = OpInfo(
            name="relu_op",
            description="ReLU activation operator",
            op_type="elementwise",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32", "float32"],
        )

        code_gen_result = CodeGenResult(
            success=True,
            files=[
                FileChange(path="kernel_relu.cpp", action="create", content="// ReLU kernel"),
                FileChange(path="test_relu.cpp", action="create", content="// Test"),
            ],
            generated_files=["kernel_relu.cpp", "test_relu.cpp"],
        )

        compile_result = CompileResult(
            success=True,
            command="make",
        )

        op_result = OpResult(
            op_info=op_info,
            code_gen_result=code_gen_result,
            compile_result=compile_result,
        )

        # 保存为技能
        saved_paths = self.saver.save(op_result, user_confirm=False)

        assert SkillDimension.TEMPLATE in saved_paths

        # 加载并验证内容
        skill = self.storage.load_skill("elementwise_relu_op")
        assert skill is not None
        # metadata 嵌套在 ascend_op_agent 下
        inner_metadata = skill.metadata.get("ascend_op_agent", {})
        assert "relu_op" in str(inner_metadata.get("op_name", ""))


class TestSkillVersioning:
    """技能版本测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_skill_version_preservation(self):
        """测试技能版本保留"""
        skill = Skill(
            name="versioned_skill",
            description="A versioned skill",
            content="Content",
            version="2.0.0",
            author="test_author",
        )

        self.storage.save_skill(skill)

        # 加载
        loaded = self.storage.load_skill("versioned_skill")

        assert loaded is not None
        assert loaded.version == "2.0.0"
        assert loaded.author == "test_author"
