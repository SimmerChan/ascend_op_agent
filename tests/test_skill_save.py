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

"""SkillSaver模块测试"""

import os
import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.skills.storage import SkillStorage
from ascend_op_agent.workflow import (
    CodeGenResult,
    CompileResult,
    OpInfo,
    PrecisionReport,
    SkillDimension,
    SkillSaver,
)
from ascend_op_agent.workflow.models import (
    FileChange,
    TestCase,
    TestResult,
)
from ascend_op_agent.workflow.skill_save import OpResult


class TestSkillDimension:
    """SkillDimension测试"""

    def test_dimension_values(self):
        """测试维度值"""
        assert SkillDimension.TEMPLATE == "template"
        assert SkillDimension.BUGFIX == "bugfix"
        assert SkillDimension.PERFORMANCE == "performance"


class TestOpResult:
    """OpResult测试"""

    def test_creation(self):
        """测试创建"""
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        result = OpResult(op_info=op_info)

        assert result.op_info.name == "test_op"
        assert result.code_gen_result is None
        assert result.compile_result is None
        assert result.precision_report is None

    def test_with_all_data(self):
        """测试完整数据"""
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="matmul",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32"],
        )
        code_gen = CodeGenResult(
            success=True,
            files=[FileChange(path="kernel.cpp", action="create", content="// test")],
        )
        compile_result = CompileResult(
            success=True,
            command="make",
        )

        op_result = OpResult(
            op_info=op_info,
            code_gen_result=code_gen,
            compile_result=compile_result,
            compile_errors=["error: missing semicolon"],
            source_files={"kernel.cpp": "// test code"},
        )

        assert op_result.code_gen_result.success is True
        assert "error: missing semicolon" in op_result.compile_errors
        assert "kernel.cpp" in op_result.source_files


class TestSkillSaver:
    """SkillSaver测试"""

    def setup_method(self):
        """每个测试方法前设置临时目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)
        self.saver = SkillSaver(storage=self.storage, author="test_author")

    def teardown_method(self):
        """每个测试方法后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_op_result(self) -> OpResult:
        """创建测试用OpResult"""
        op_info = OpInfo(
            name="test_element",
            description="Test elementwise operator",
            op_type="elementwise",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32", "float32"],
        )
        code_gen = CodeGenResult(
            success=True,
            files=[
                FileChange(path="kernel_test_element.cpp", action="create", content="// kernel"),
                FileChange(path="kernel_test_element_host.cpp", action="create", content="// host"),
            ],
            generated_files=["kernel_test_element.cpp", "kernel_test_element_host.cpp"],
        )
        compile_result = CompileResult(
            success=True,
            command="make",
            syntax_errors=["error: expected ';'"],
            missing_headers=["header not found"],
        )

        # 创建一些测试结果
        test_results = [
            TestResult(
                test_case=TestCase(
                    name=f"test_{i}",
                    input_shapes=[[32, 32]],
                    input_dtypes=["float32"],
                    expected_shapes=[[32, 32]],
                    expected_dtypes=["float32"],
                ),
                passed=True,
                abs_err=0.001 * i,
                rel_err=0.001 * i,
                cos_sim=0.999,
            )
            for i in range(30)
        ]

        precision_report = PrecisionReport(
            operator_name="test_element",
            total_cases=30,
            passed_cases=28,
            failed_cases=2,
            test_results=test_results,
        )
        precision_report.calculate_summary()

        return OpResult(
            op_info=op_info,
            code_gen_result=code_gen,
            compile_result=compile_result,
            precision_report=precision_report,
            compile_errors=["error: missing semicolon"],
            source_files={
                "kernel_test_element.cpp": "// kernel code\nvoid main() {}\n",
                "kernel_test_element_host.cpp": "// host code\n",
            },
            performance_data={"throughput": "100 GFLOPS"},
        )

    def test_extract_template(self):
        """测试提取模板技能"""
        op_result = self._create_test_op_result()

        skill = self.saver._extract_template(op_result)

        assert skill is not None
        assert skill.name == "elementwise_test_element"
        assert "elementwise" in skill.tags
        assert "template" in skill.tags
        assert skill.author == "test_author"
        assert skill.version == "1.0.0"
        assert "test_element" in skill.content
        assert "kernel_test_element.cpp" in skill.content

    def test_extract_bugfix(self):
        """测试提取Bugfix技能"""
        op_result = self._create_test_op_result()

        skill = self.saver._extract_bugfix(op_result)

        assert skill is not None
        assert skill.name == "elementwise_test_element"  # 基础名称，storage.save_skill会添加_bugfix后缀
        assert "bugfix" in skill.tags
        assert "error: missing semicolon" in skill.content

    def test_extract_performance(self):
        """测试提取性能优化技能"""
        op_result = self._create_test_op_result()

        skill = self.saver._extract_performance(op_result)

        assert skill is not None
        assert skill.name == "elementwise_test_element"  # 基础名称，storage.save_skill会添加_performance后缀
        assert "performance" in skill.tags
        assert "Tiling" in skill.content

    def test_save_all_dimensions(self):
        """测试保存所有维度"""
        op_result = self._create_test_op_result()

        saved_paths = self.saver.save(op_result, user_confirm=False)

        # 应该保存了3个维度
        assert SkillDimension.TEMPLATE in saved_paths
        assert SkillDimension.BUGFIX in saved_paths
        assert SkillDimension.PERFORMANCE in saved_paths

        # 验证文件确实存在
        assert (Path(saved_paths[SkillDimension.TEMPLATE]) / "SKILL.md").exists()
        assert (Path(saved_paths[SkillDimension.BUGFIX]) / "SKILL.md").exists()
        assert (Path(saved_paths[SkillDimension.PERFORMANCE]) / "SKILL.md").exists()

    def test_save_single_dimension(self):
        """测试保存单个维度"""
        op_result = self._create_test_op_result()

        saved_paths = self.saver.save(
            op_result,
            dimensions=[SkillDimension.TEMPLATE],
            user_confirm=False,
        )

        assert len(saved_paths) == 1
        assert SkillDimension.TEMPLATE in saved_paths
        assert SkillDimension.BUGFIX not in saved_paths
        assert SkillDimension.PERFORMANCE not in saved_paths

    def test_save_without_bugfix_errors(self):
        """测试没有bugfix错误时不保存bugfix"""
        op_info = OpInfo(
            name="clean_op",
            description="Clean operator",
            op_type="elementwise",
        )
        op_result = OpResult(op_info=op_info)  # 没有compile_errors

        saved_paths = self.saver.save(op_result, user_confirm=False)

        # 只有template和performance，因为没有bugfix数据
        assert SkillDimension.TEMPLATE in saved_paths
        assert SkillDimension.BUGFIX not in saved_paths

    def test_storage_integration(self):
        """测试存储集成"""
        op_result = self._create_test_op_result()

        self.saver.save(op_result, user_confirm=False)

        # 验证存储中有技能
        skills = self.storage.list_skills()
        assert len(skills) == 3
        assert "elementwise_test_element" in skills
        assert "elementwise_test_element_bugfix" in skills
        assert "elementwise_test_element_performance" in skills

    def test_skill_content_format(self):
        """测试SKILL.md格式"""
        op_result = self._create_test_op_result()

        self.saver.save(
            op_result,
            dimensions=[SkillDimension.TEMPLATE],
            user_confirm=False,
        )

        skill_file = Path(self.temp_dir) / "elementwise_test_element" / "SKILL.md"
        content = skill_file.read_text()

        # 验证frontmatter格式
        assert content.startswith("---")
        assert "name: elementwise_test_element" in content
        assert "description:" in content
        assert "version:" in content
        assert "author: test_author" in content
        assert "---" in content

    def test_load_saved_skill(self):
        """测试加载保存的技能"""
        op_result = self._create_test_op_result()

        self.saver.save(op_result, user_confirm=False)

        # 从存储加载
        skill = self.storage.load_skill("elementwise_test_element")

        assert skill is not None
        assert skill.name == "elementwise_test_element"
        assert skill.author == "test_author"


class TestSkillSaverPublish:
    """SkillSaver发布功能测试"""

    def setup_method(self):
        """每个测试方法前设置临时目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)
        self.saver = SkillSaver(storage=self.storage)

    def teardown_method(self):
        """每个测试方法后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_publish_skill_not_found(self):
        """测试发布不存在的技能"""
        result = self.saver.publish(
            skill_name="nonexistent_skill",
            remote_repo="https://gitcode.com/test/repo",
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_create_pr_invalid_url(self):
        """测试无效仓库URL创建PR"""
        result = self.saver.create_pr(
            skill_name="test_skill",
            remote_repo="invalid-url",
        )

        assert result["success"] is False


class TestSkillSaverWithOpInfo:
    """SkillSaver与OpInfo集成测试"""

    def setup_method(self):
        """每个测试方法前设置临时目录"""
        self.temp_dir = tempfile.mkdtemp()
        self.storage = SkillStorage(skills_dir=self.temp_dir)
        self.saver = SkillSaver(storage=self.storage)

    def teardown_method(self):
        """每个测试方法后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_matmul_op(self):
        """测试矩阵乘算子"""
        op_info = OpInfo(
            name="my_matmul",
            description="Matrix multiplication operator",
            op_type="matmul",
            input_shapes=[[512, 256], [256, 128]],
            input_dtypes=["float16", "float16"],
        )
        op_result = OpResult(op_info=op_info)

        saved_paths = self.saver.save(op_result, user_confirm=False)

        # 验证模板技能名称包含算子类型和名称
        template_path = saved_paths[SkillDimension.TEMPLATE]
        assert "matmul_my_matmul" in template_path

    def test_reduction_op(self):
        """测试归约算子"""
        op_info = OpInfo(
            name="my_reduce",
            description="Reduction operator",
            op_type="reduction",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32"],
        )
        op_result = OpResult(op_info=op_info)

        saved_paths = self.saver.save(op_result, user_confirm=False)

        assert SkillDimension.TEMPLATE in saved_paths
        # reduction没有编译错误，所以没有bugfix
        assert SkillDimension.BUGFIX not in saved_paths

    def test_migration_scenario(self):
        """测试GPU迁移场景"""
        from ascend_op_agent.workflow.models import MigrationStrategy

        op_info = OpInfo(
            name="cuda_add",
            description="CUDA to AscendC migration",
            op_type="elementwise",
            ref_code_path="/path/to/cuda.cu",
            ref_code_type="cuda",
            migration_strategy=MigrationStrategy.CUDA_TO_ASCENDC,
        )
        op_result = OpResult(op_info=op_info)

        skill = self.saver._extract_template(op_result)

        assert skill is not None
        # 验证metadata中包含迁移信息
        assert skill.metadata.get("op_name") == "cuda_add"
        assert skill.metadata.get("op_type") == "elementwise"
