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

"""自改进闭环集成测试

测试完整流程:
1. 算子开发 → 自动保存 Skill
2. 新会话检索历史 Skill
3. 性能报告生成
"""

import tempfile

import pytest

from ascend_op_agent.workflow.engine import OperatorWorkflow, create_workflow
from ascend_op_agent.workflow.phases import Phase7SkillSave, Phase8Performance


class TestSelfImprovementLoop:
    """自改进闭环集成测试"""

    def test_workflow_with_skill_save_and_performance(self):
        """测试带技能保存和性能评测的完整工作流"""
        workflow = create_workflow(
            enable_phase5=True,
            enable_phase7=True,
            enable_phase8=True,
        )

        # 验证所有阶段都存在
        phase_names = [p.name for p in workflow.phases]
        assert "Phase0_Init" in phase_names
        assert "Phase1_Analysis" in phase_names
        assert "Phase2_Design" in phase_names
        assert "Phase3_CodeGen" in phase_names
        assert "Phase4_Verify" in phase_names
        assert "Phase5_Precision" in phase_names
        assert "Phase7_SkillSave" in phase_names
        assert "Phase8_Performance" in phase_names

    def test_workflow_without_phase5(self):
        """测试禁用Phase5时的工作流仍有Phase7和Phase8"""
        workflow = create_workflow(
            enable_phase5=False,
            enable_phase7=True,
            enable_phase8=True,
        )

        phase_names = [p.name for p in workflow.phases]
        assert "Phase0_Init" in phase_names
        assert "Phase5_Precision" not in phase_names
        assert "Phase7_SkillSave" in phase_names
        assert "Phase8_Performance" in phase_names

    def test_workflow_without_phase7(self):
        """测试禁用Phase7时的工作流"""
        workflow = create_workflow(
            enable_phase5=True,
            enable_phase7=False,
            enable_phase8=True,
        )

        phase_names = [p.name for p in workflow.phases]
        assert "Phase7_SkillSave" not in phase_names
        assert "Phase8_Performance" in phase_names

    def test_workflow_without_phase8(self):
        """测试禁用Phase8时的工作流"""
        workflow = create_workflow(
            enable_phase5=True,
            enable_phase7=True,
            enable_phase8=False,
        )

        phase_names = [p.name for p in workflow.phases]
        assert "Phase7_SkillSave" in phase_names
        assert "Phase8_Performance" not in phase_names

    def test_phase7_skill_save_trigger(self):
        """测试Phase7可以被触发"""
        phase7 = Phase7SkillSave(auto_save=True)

        # 模拟上下文（无op_result）
        context = {}

        result = phase7.execute(context)

        # 应该失败因为没有op_result
        assert result.status.value == "failed"
        assert "No op_result in context" in result.errors

    def test_phase8_performance_trigger(self):
        """测试Phase8可以被触发"""
        phase8 = Phase8Performance()

        # 模拟上下文
        from ascend_op_agent.workflow.models import OpInfo
        op_info = OpInfo(
            name="test_op",
            description="Test operator",
            op_type="elementwise",
            input_shapes=[[1024, 1024]],
            input_dtypes=["float32"],
        )

        context = {"op_info": op_info}

        result = phase8.execute(context)

        # 应该成功
        assert result.status.value == "completed"
        assert "report" in result.data
        assert result.data["report"].operator_name == "test_op"

    def test_all_phases_disabled(self):
        """测试所有可选阶段都被禁用"""
        workflow = create_workflow(
            enable_phase5=False,
            enable_phase7=False,
            enable_phase8=False,
        )

        phase_names = [p.name for p in workflow.phases]
        # 只有Phase0-4
        assert len(phase_names) == 5
        assert "Phase0_Init" in phase_names
        assert "Phase4_Verify" in phase_names
        assert "Phase5_Precision" not in phase_names
        assert "Phase7_SkillSave" not in phase_names
        assert "Phase8_Performance" not in phase_names

    def test_skill_save_auto_save_mode(self):
        """测试技能自动保存模式"""
        phase7_auto = Phase7SkillSave(auto_save=True)
        phase7_manual = Phase7SkillSave(auto_save=False)

        # 自动保存不需要确认
        assert phase7_auto.requires_confirmation() is False
        # 手动保存需要确认
        assert phase7_manual.requires_confirmation() is True

    def test_performance_report_generation(self):
        """测试性能报告生成"""
        phase8 = Phase8Performance()

        from ascend_op_agent.workflow.models import OpInfo
        op_info = OpInfo(
            name="matmul_op",
            description="Matrix multiplication operator",
            op_type="matmul",
            input_shapes=[[512, 512]],
            input_dtypes=["float32"],
        )

        context = {"op_info": op_info}
        result = phase8.execute(context)

        assert result.is_success()
        report = result.data["report"]
        assert report.operator_name == "matmul_op"
        assert report.total_cases > 0
        assert len(report.metrics) > 0
