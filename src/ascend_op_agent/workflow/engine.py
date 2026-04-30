# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a approval.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OperatorWorkflow - 工作流引擎"""

import logging
from typing import Any, Generator, Optional

from ascend_op_agent.workflow.models import PhaseResult, PhaseStatus
from ascend_op_agent.workflow.phases import (
    Phase,
    Phase0Init,
    Phase1Analysis,
    Phase2Design,
    Phase3CodeGen,
    Phase4Verify,
    Phase5Precision,
)

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    """工作流错误"""
    pass


class OperatorWorkflow:
    """算子开发工作流引擎

    协调各Phase执行，处理用户确认和迭代控制。

    Phase流程:
    1. Phase0Init - 初始化
    2. Phase1Analysis - 需求分析
    3. Phase2Design - 方案设计（需要用户确认）
    4. Phase3CodeGen - 代码生成
    5. Phase4Verify - 编译验证
    6. Phase5Precision - 精度评估（必选）
    """

    def __init__(
        self,
        enable_phase5_precision: bool = True,
        max_compile_fix_attempts: int = 3,
    ):
        """
        Args:
            enable_phase5_precision: 是否启用Phase5精度评估（必选）
            max_compile_fix_attempts: 最大编译修复次数
        """
        self.phases: list[Phase] = [
            Phase0Init(),
            Phase1Analysis(),
            Phase2Design(),
            Phase3CodeGen(),
            Phase4Verify(),
        ]

        if enable_phase5_precision:
            self.phases.append(Phase5Precision())

        self.max_compile_fix_attempts = max_compile_fix_attempts
        self._current_phase_index = 0
        self._context: dict[str, Any] = {}
        self._phase_results: list[PhaseResult] = []

    def run(self, user_input: str) -> Generator[PhaseResult, Optional[bool], None]:
        """运行工作流

        Args:
            user_input: 用户输入

        Yields:
            PhaseResult - 每个阶段的执行结果

       接受用户确认（对于requires_confirmation的阶段）:
            True - 确认并继续
            False - 拒绝并终止
            None - 继续等待
        """
        # 初始化上下文
        self._context = {"user_input": user_input}
        self._phase_results = []
        self._current_phase_index = 0

        for phase in self.phases:
            self._current_phase_index = self.phases.index(phase)

            # 执行阶段
            result = phase.execute(self._context)
            self._phase_results.append(result)

            # 如果需要确认，等待用户输入
            if result.requires_confirmation():
                user_confirm = yield result

                # 处理用户确认
                if user_confirm is False:
                    # 用户拒绝，终止工作流
                    logger.info(f"User rejected at {phase.name}, workflow terminated")
                    return

                # 用户确认，继续执行
                if hasattr(phase, "confirm"):
                    confirm_result = phase.confirm(self._context, user_confirm)
                    if not confirm_result.is_success():
                        return

            elif not result.is_success():
                # 阶段失败，终止工作流
                logger.warning(f"Phase {phase.name} failed: {result.errors}")
                return

            logger.info(f"Phase {phase.name} completed successfully")

        # 工作流完成
        logger.info("Workflow completed")

    def get_context(self) -> dict[str, Any]:
        """获取工作流上下文"""
        return self._context.copy()

    def get_phase_results(self) -> list[PhaseResult]:
        """获取所有阶段的结果"""
        return self._phase_results.copy()

    def get_current_phase_index(self) -> int:
        """获取当前阶段索引"""
        return self._current_phase_index

    def get_current_phase(self) -> Optional[Phase]:
        """获取当前阶段"""
        if 0 <= self._current_phase_index < len(self.phases):
            return self.phases[self._current_phase_index]
        return None

    def reset(self) -> None:
        """重置工作流状态"""
        self._context = {}
        self._phase_results = []
        self._current_phase_index = 0

        # 重置所有阶段
        for phase in self.phases:
            phase._result = None


class WorkflowRunner:
    """工作流运行器（简化版）"""

    def __init__(self, workflow: OperatorWorkflow):
        self.workflow = workflow

    def run_interactive(self, user_input: str) -> dict[str, Any]:
        """交互式运行工作流

        Args:
            user_input: 用户输入

        Returns:
            最终的工作流上下文
        """
        generator = self.workflow.run(user_input)

        try:
            while True:
                result = next(generator)

                # 打印阶段结果
                print(f"\n{'='*60}")
                print(f"Phase: {result.phase_name}")
                print(f"Status: {result.status.value}")
                print(f"Message: {result.message}")
                if result.errors:
                    print(f"Errors: {result.errors}")
                print(f"{'='*60}\n")

                # 如果需要确认
                if result.requires_confirmation():
                    user_input = input("确认方案? (y/n): ").strip().lower()
                    if user_input in ("y", "yes"):
                        generator.send(True)
                    else:
                        generator.send(False)
                        break

        except StopIteration:
            # 工作流完成
            pass

        return self.workflow.get_context()


def create_workflow(
    enable_phase5: bool = True,
    max_compile_fixes: int = 3,
) -> OperatorWorkflow:
    """创建工作流实例的工厂函数

    Args:
        enable_phase5: 是否启用Phase5精度评估
        max_compile_fixes: 最大编译修复次数

    Returns:
        OperatorWorkflow实例
    """
    return OperatorWorkflow(
        enable_phase5_precision=enable_phase5,
        max_compile_fix_attempts=max_compile_fixes,
    )
