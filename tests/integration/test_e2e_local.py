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

"""本地模式端到端集成测试

测试完整的本地开发模式工作流。
"""

import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.workflow import (
    OperatorWorkflow,
    create_workflow,
)
from ascend_op_agent.workflow.models import (
    CodeGenResult,
    CompileResult,
    OpInfo,
    PrecisionReport,
    TestResult,
    TestCase,
)


class TestE2ELocalWorkflow:
    """本地模式端到端工作流测试"""

    def setup_method(self):
        """每个测试前设置"""
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir) / "workspace"
        self.workspace.mkdir()

    def teardown_method(self):
        """每个测试后清理"""
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_workflow_elementwise(self):
        """测试完整的elementwise算子开发流程"""
        workflow = create_workflow(enable_phase5=True)

        # 模拟用户输入
        user_input = "实现一个elementwise算子，输入shape为[1024,1024]，dtype为float32"

        # 使用生成器运行工作流
        gen = workflow.run(user_input)

        # 工作流会停在第一个需要确认的阶段（Phase2_Design）
        results = []
        try:
            while True:
                result = gen.send(None)
                results.append(result)
                if result.requires_confirmation():
                    results.append(gen.send(True))
        except StopIteration:
            pass

        # 验证至少执行了一些阶段
        assert len(results) > 0

        # 验证上下文包含 op_info
        context = workflow.get_context()
        assert "op_info" in context
        assert context["op_info"].op_type == "elementwise"

    def test_workflow_phase5_disabled(self):
        """测试禁用Phase5时的工作流"""
        workflow = create_workflow(enable_phase5=False)

        assert len(workflow.phases) == 5  # 没有Phase5

        user_input = "实现一个elementwise算子"

        gen = workflow.run(user_input)

        results = []
        try:
            while True:
                result = gen.send(None)
                results.append(result)
                if result.requires_confirmation():
                    results.append(gen.send(True))
        except StopIteration:
            pass

        # 验证没有Phase5
        phase_names = [r.phase_name for r in results]
        assert "Phase5_Precision" not in phase_names


class TestE2ELocalWithRealPhases:
    """使用真实Phase实现的本地模式测试"""

    def test_phase0_parses_user_input_correctly(self):
        """测试Phase0正确解析用户输入"""
        from ascend_op_agent.workflow.phases import Phase0Init

        phase = Phase0Init()
        context = {"user_input": "实现一个矩阵乘算子，输入shape为[128,256]和[256,64]，dtype为float32"}

        result = phase.execute(context)

        assert result.is_success()
        op_info = result.data["op_info"]
        assert op_info.op_type == "matmul"

    def test_phase1_generates_analysis_report(self):
        """测试Phase1生成分析报告"""
        from ascend_op_agent.workflow.phases import Phase0Init, Phase1Analysis

        phase0 = Phase0Init()
        context = {"user_input": "实现一个elementwise算子"}
        phase0.execute(context)

        phase1 = Phase1Analysis()
        result = phase1.execute(context)

        assert result.is_success()
        assert "analysis_report" in context
        assert "report" in result.data

    def test_phase3_generates_expected_files(self):
        """测试Phase3生成预期文件"""
        from ascend_op_agent.workflow.phases import Phase0Init, Phase1Analysis, Phase2Design, Phase3CodeGen
        from ascend_op_agent.workflow.models import DesignDoc

        context = {"user_input": "实现一个elementwise算子"}

        phase0 = Phase0Init()
        phase0.execute(context)

        phase1 = Phase1Analysis()
        phase1.execute(context)

        op_info = context["op_info"]
        context["design_doc"] = DesignDoc(
            op_info=op_info,
            input_layouts=["ROW_MAJOR"],
            output_layouts=["ROW_MAJOR"],
        )

        phase3 = Phase3CodeGen()
        result = phase3.execute(context)

        assert result.is_success()
        code_gen_result = result.data["result"]

        filenames = [f.path for f in code_gen_result.files]
        assert any("kernel_" in f for f in filenames)
        assert any("test_" in f for f in filenames)
        assert any("CMakeLists.txt" in f for f in filenames)


class TestWorkflowResultAggregation:
    """工作流结果聚合测试"""

    def test_all_phase_results_recorded(self):
        """测试所有阶段结果都被记录"""
        workflow = create_workflow()

        user_input = "实现一个elementwise算子"

        gen = workflow.run(user_input)

        results = []
        try:
            while True:
                result = gen.send(None)
                results.append(result)
                if result.requires_confirmation():
                    result = gen.send(True)
                    results.append(result)
        except StopIteration:
            pass

        phase_results = workflow.get_phase_results()
        assert len(phase_results) >= 5

        phase_names = [r.phase_name for r in phase_results]
        assert "Phase0_Init" in phase_names
        assert "Phase1_Analysis" in phase_names

    def test_context_contains_all_artifacts(self):
        """测试上下文包含所有产物"""
        workflow = create_workflow()

        user_input = "实现一个elementwise算子"

        gen = workflow.run(user_input)

        try:
            while True:
                result = gen.send(None)
                if result.requires_confirmation():
                    result = gen.send(True)
        except StopIteration:
            pass

        context = workflow.get_context()

        assert "op_info" in context
        assert "code_gen_result" in context
        assert "compile_result" in context
