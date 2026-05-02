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

"""Workflow模块测试"""

import pytest

from ascend_op_agent.workflow import (
    ArchitectureMapping,
    CodeGenResult,
    CompileResult,
    DesignDoc,
    MigrationStrategy,
    OpInfo,
    OperatorWorkflow,
    Phase0Init,
    Phase1Analysis,
    Phase2Design,
    Phase3CodeGen,
    Phase4Verify,
    Phase5Precision,
    PhaseResult,
    PhaseStatus,
    PrecisionReport,
    WorkflowError,
    create_workflow,
)
from ascend_op_agent.workflow.models import FileChange, TestCase, TestResult


class TestMigrationStrategy:
    """MigrationStrategy测试"""

    def test_enum_values(self):
        """测试枚举值"""
        assert MigrationStrategy.FROM_SCRATCH.value == "from_scratch"
        assert MigrationStrategy.CUDA_TO_ASCENDC.value == "cuda_to_ascendc"
        assert MigrationStrategy.TRITON_TO_ASCENDC.value == "triton_to_ascendc"


class TestArchitectureMapping:
    """ArchitectureMapping测试"""

    def test_get_cuda_mapping(self):
        """测试CUDA映射"""
        mapping = ArchitectureMapping.get_mapping("cuda")

        assert mapping.source_type == "cuda"
        assert "shared_memory" in mapping.mappings
        assert mapping.mappings["shared_memory"] == "Global Memory"

    def test_get_triton_mapping(self):
        """测试Triton映射"""
        mapping = ArchitectureMapping.get_mapping("triton")

        assert mapping.source_type == "triton"
        assert "tl.load" in mapping.mappings
        assert mapping.mappings["tl.load"] == "LoadTensor"


class TestOpInfo:
    """OpInfo测试"""

    def test_creation(self):
        """测试创建"""
        op = OpInfo(
            name="test_op",
            description="A test operator",
            op_type="elementwise",
        )

        assert op.name == "test_op"
        assert op.op_type == "elementwise"
        assert op.migration_strategy == MigrationStrategy.FROM_SCRATCH

    def test_detect_migration_scenario(self):
        """测试GPU迁移场景检测"""
        op = OpInfo(
            name="test_op",
            description="Test",
            op_type="elementwise",
            ref_code_path="/path/to/cuda/code.cu",
            ref_code_type="cuda",
        )

        assert op.detect_migration_scenario() is True

    def test_get_complexity_score(self):
        """测试复杂度评估"""
        op = OpInfo(
            name="test_op",
            description="Test",
            op_type="matmul",
            input_shapes=[[1024, 1024], [1024, 1024]],
            input_dtypes=["float32", "float32"],
        )

        score = op.get_complexity_score()
        assert score >= 4


class TestPhase0Init:
    """Phase0Init测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase0Init()
        context = {"user_input": "实现一个elementwise算子"}

        result = phase.execute(context)

        assert result.is_success()
        assert "op_info" in result.data
        assert result.data["op_info"].name == "unknown_op"

    def test_parse_elementwise(self):
        """测试解析elementwise算子"""
        phase = Phase0Init()
        context = {"user_input": "实现一个elementwise算子，输入shape为[1024,1024]，dtype为float32"}

        result = phase.execute(context)

        assert result.is_success()
        op_info = result.data["op_info"]
        assert op_info.op_type == "elementwise"

    def test_parse_matmul(self):
        """测试解析matmul算子"""
        phase = Phase0Init()
        context = {"user_input": "实现一个矩阵乘算子，输入shape为[512,256]和[256,128]"}

        result = phase.execute(context)

        assert result.is_success()
        op_info = result.data["op_info"]
        assert op_info.op_type == "matmul"


class TestPhase1Analysis:
    """Phase1Analysis测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase1Analysis()
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        context = {"op_info": op_info}

        result = phase.execute(context)

        assert result.is_success()
        assert "op_info" in result.data
        assert "complexity" in result.data


class TestPhase2Design:
    """Phase2Design测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase2Design()
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        context = {"op_info": op_info}

        result = phase.execute(context)

        assert result.requires_confirmation()
        assert result.status == PhaseStatus.WAITING_CONFIRMATION
        assert "design_doc" in result.data

    def test_confirm(self):
        """测试确认"""
        phase = Phase2Design()
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        context = {"op_info": op_info, "design_doc": DesignDoc(op_info=op_info)}

        result = phase.execute(context)
        confirm_result = phase.confirm(context, True)

        assert confirm_result.is_success()


class TestPhase3CodeGen:
    """Phase3CodeGen测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase3CodeGen()
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)
        context = {"op_info": op_info, "design_doc": design_doc}

        result = phase.execute(context)

        assert result.is_success()
        assert result.data.get("result") is not None
        assert result.data["result"].success is True

    def test_generated_files(self):
        """测试生成的文件"""
        phase = Phase3CodeGen()
        op_info = OpInfo(
            name="my_op",
            description="Test operator",
            op_type="elementwise",
        )
        design_doc = DesignDoc(op_info=op_info)
        context = {"op_info": op_info, "design_doc": design_doc}

        result = phase.execute(context)

        files = result.data["result"].generated_files
        assert "kernel_my_op.cpp" in files
        assert "test_my_op.cpp" in files
        assert "CMakeLists.txt" in files


class TestPhase4Verify:
    """Phase4Verify测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase4Verify()
        code_gen_result = CodeGenResult(success=True, files=[])
        context = {"code_gen_result": code_gen_result}

        result = phase.execute(context)

        assert result.is_success()


class TestPhase5Precision:
    """Phase5Precision测试"""

    def test_execute(self):
        """测试执行"""
        phase = Phase5Precision(min_test_cases=30)
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
        )
        compile_result = CompileResult(success=True, command="make")
        context = {"op_info": op_info, "compile_result": compile_result}

        result = phase.execute(context)

        # Phase5可能PASSED或FAILED，取决于随机测试结果
        assert "report" in result.data
        report = result.data["report"]
        assert report.total_cases == 30

    def test_report_generation(self):
        """测试报告生成"""
        phase = Phase5Precision(min_test_cases=10)
        test_results = [
            TestResult(
                test_case=TestCase(
                    name="test1",
                    input_shapes=[[10, 10]],
                    input_dtypes=["float32"],
                    expected_shapes=[[10, 10]],
                    expected_dtypes=["float32"],
                ),
                passed=True,
                abs_err=0.001,
                rel_err=0.001,
                cos_sim=0.999,
            )
        ]

        report = PrecisionReport(
            operator_name="test_op",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            test_results=test_results,
        )
        report.calculate_summary()

        assert report.meets_requirement is False  # 需要至少30个用例


class TestOperatorWorkflow:
    """OperatorWorkflow测试"""

    def test_creation(self):
        """测试创建"""
        workflow = OperatorWorkflow()

        assert len(workflow.phases) == 8  # Phase0-5 + Phase7 + Phase8
        assert workflow.get_current_phase_index() == 0

    def test_creation_without_phase5(self):
        """测试不启用Phase5"""
        workflow = OperatorWorkflow(enable_phase5_precision=False)

        assert len(workflow.phases) == 7  # Phase0-4 + Phase7 + Phase8

    def test_run_generator(self):
        """测试工作流执行（生成器模式）"""
        workflow = OperatorWorkflow()

        # 测试Phase0 - 使用send(None)来启动生成器
        # 工作流会执行到第一个需要确认的阶段（Phase2_Design）
        gen = workflow.run("实现一个elementwise算子")
        result = gen.send(None)

        # Phase2需要确认，所以会停在这里
        assert result.phase_name == "Phase2_Design"
        assert result.requires_confirmation()


class TestCodeGenResult:
    """CodeGenResult测试"""

    def test_creation(self):
        """测试创建"""
        result = CodeGenResult(
            success=True,
            files=[FileChange(path="test.cpp", action="create", content="// test")],
            generated_files=["test.cpp"],
        )

        assert result.success is True
        assert len(result.generated_files) == 1

    def test_to_dict(self):
        """测试转换为字典"""
        result = CodeGenResult(
            success=True,
            files=[FileChange(path="test.cpp", action="create")],
        )

        d = result.to_dict()
        assert d["success"] is True
        assert len(d["files"]) == 1


class TestCompileResult:
    """CompileResult测试"""

    def test_can_fix(self):
        """测试是否可修复"""
        result = CompileResult(
            success=False,
            command="make",
            syntax_errors=["error: expected ';'"],
        )

        assert result.can_fix() is True
        assert result.should_stop_fixing() is False

    def test_max_fix_attempts(self):
        """测试最大修复次数"""
        result = CompileResult(
            success=False,
            command="make",
            syntax_errors=["error"],
            fix_attempts=3,
        )

        assert result.should_stop_fixing() is True


class TestPrecisionReport:
    """PrecisionReport测试"""

    def test_calculate_summary(self):
        """测试计算汇总"""
        test_results = [
            TestResult(
                test_case=TestCase(
                    name="test1",
                    input_shapes=[[10]],
                    input_dtypes=["float32"],
                    expected_shapes=[[10]],
                    expected_dtypes=["float32"],
                ),
                passed=True,
                abs_err=0.001,
                rel_err=0.001,
                cos_sim=0.999,
            ),
            TestResult(
                test_case=TestCase(
                    name="test2",
                    input_shapes=[[10]],
                    input_dtypes=["float32"],
                    expected_shapes=[[10]],
                    expected_dtypes=["float32"],
                ),
                passed=True,
                abs_err=0.002,
                rel_err=0.002,
                cos_sim=0.998,
            ),
        ]

        report = PrecisionReport(
            operator_name="test_op",
            total_cases=30,  # 满足最低要求
            passed_cases=30,
            failed_cases=0,
            test_results=test_results,
        )

        report.calculate_summary()

        assert report.avg_abs_err is not None
        assert report.meets_requirement is True

    def test_to_markdown(self):
        """测试转换为Markdown"""
        test_results = [
            TestResult(
                test_case=TestCase(
                    name="test1",
                    input_shapes=[[10]],
                    input_dtypes=["float32"],
                    expected_shapes=[[10]],
                    expected_dtypes=["float32"],
                ),
                passed=True,
                abs_err=0.001,
                rel_err=0.001,
                cos_sim=0.999,
            ),
        ]

        report = PrecisionReport(
            operator_name="test_op",
            total_cases=30,
            passed_cases=30,
            failed_cases=0,
            test_results=test_results,
        )

        md = report.to_markdown()

        assert "# Precision Report: test_op" in md
        assert "**Total Cases**: 30" in md
        assert "**Passed**: 30" in md


class TestCreateWorkflow:
    """create_workflow工厂函数测试"""

    def test_create_default(self):
        """测试默认创建"""
        workflow = create_workflow()

        assert len(workflow.phases) == 8  # Phase0-5 + Phase7 + Phase8
        assert workflow.max_compile_fix_attempts == 3

    def test_create_without_phase5(self):
        """测试不启用Phase5"""
        workflow = create_workflow(enable_phase5=False)

        assert len(workflow.phases) == 7  # Phase0-4 + Phase7 + Phase8

    def test_create_with_custom_fixes(self):
        """测试自定义最大修复次数"""
        workflow = create_workflow(max_compile_fixes=5)

        assert workflow.max_compile_fix_attempts == 5
