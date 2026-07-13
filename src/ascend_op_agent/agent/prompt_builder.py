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

"""PromptBuilder - 7层Prompt组装器

参考Hermes Agent的7层Prompt Assembly设计:
1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory (MemoryStore持久化)
6. Skills Index
7. Context Files + Timestamp + Env
"""

import os
from pathlib import Path
from typing import List, Optional

from ascend_op_agent.agent.memory import MemoryStore


# PR-A KTD-2 + A1 fix: self-built degradation threshold (实测预算驱动).
# cannbot phase subset (production max ~266 tok for triton_frontend) +
# 12 self-built × ~40 tok ≈ 480 tok ≈ total 746 tok < 800 ship gate.
SELF_BUILT_DEGRADE_THRESHOLD = 12


class PromptBuilder:
    """7层Prompt组装器"""

    def __init__(self, soul_md_path: Optional[str] = None):
        """
        Args:
            soul_md_path: SOUL.md文件路径，默认为agent目录下的SOUL.md
        """
        if soul_md_path is None:
            self._soul_path = Path(__file__).parent / "SOUL.md"
        else:
            self._soul_path = Path(soul_md_path)

    def build_system_prompt(
        self,
        workspace_path: str,
        memory_store: MemoryStore,
        skills_layer_override: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> str:
        """构建完整的系统Prompt（7层组装）

        Args:
            workspace_path: 工作区路径
            memory_store: 记忆存储
            skills_layer_override: 可选,注入该阶段 cannbot skill 包替换默认 Layer 6。
                由编排器 LLM 节点调用时传入(hybrid 集成的编排层入口);
            task_type: 可选,任务类型(``develop``/``migrate``/``analyze``/``optimize``)
                用于 Layer 6 降级决策(U2 实现)。PR-A 阶段(U1)仅 plumbing:U2
                重写 ``_build_skills_layer`` 时按此值分流,默认 None 走"只 self-built"
                路径。

        Returns:
            组装后的完整系统Prompt
        """
        layers = []

        # Layer 1: Agent Identity (SOUL.md)
        layers.append(self._build_identity_layer())

        # Layer 2: Hermes Help Guidance
        layers.append(self._build_agent_guidance())

        # Layer 3: Tool-aware Behavioral Guidance
        layers.append(self._build_tool_guidance())

        # Layer 4: Custom System Message
        layers.append(self._build_custom_message())

        # Layer 5: Persistent Memory
        layers.append(self._build_memory_layer(memory_store))

        # Layer 6: Skills Index(支持编排器 scope 注入 cannbot skill 包)
        # U1:把 override 和 task_type 一并传给 _build_skills_layer,
        # 留给 U2 按 task_type 决定降级策略。当前(U1)该函数暂未实现
        # task_type 分流,签名先就位。
        layers.append(self._build_skills_layer(skills_layer_override, task_type))

        # Layer 7: Context Files + Timestamp + Env
        layers.append(self._build_context_layer(workspace_path))

        return "\n\n".join(filter(None, layers))

    def _build_identity_layer(self) -> str:
        """Layer 1: Agent Identity"""
        if self._soul_path.exists():
            with open(self._soul_path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _build_agent_guidance(self) -> str:
        """Layer 2: Agent Guidance"""
        return """## Agent Guidance
如果用户询问关于配置、设置或使用Ascend Oo Agent​ 本身的问题，参考项目根目录的README.md中的使用指南部分进行回答。
"""

    def _build_tool_guidance(self) -> str:
        """Layer 3: Tool-aware Behavioral Guidance"""
        return """## Tool Usage

当需要执行操作时，你可以调用工具。工具参数将根据其 schema 进行验证。

重要:
- 工具调用后等待结果再继续
- 错误时重试或尝试替代方案
- 敏感操作需用户确认
"""

    def _build_custom_message(self) -> str:
        """Layer 4: Custom System Message"""
        return """## 昇腾算子开发规范

### 场景支持
1. 从0开发算子: 基于用户描述的算子逻辑进行开发
2. GPU迁移算子: 从CUDA/CUTLASS/Triton迁移到AscendC

### 开发模式
1. 本地开发: 直接在本地环境开发
2. 远程开发: 通过SSH连接远程服务器开发

### 工作流阶段
- Phase 0: 初始化（环境检测）
- Phase 1: 需求分析（自动）
- Phase 2: 方案设计（需用户确认）
- Phase 3: 代码生成
- Phase 4: 编译验证
- Phase 5: 精度评估
- Phase 6: 框架适配（可选）
- Phase 7: 技能保存（可选）
- Phase 8: 性能评测
"""

    def _build_memory_layer(self, memory_store: MemoryStore) -> str:
        """Layer 5: Persistent Memory"""
        memory_content = memory_store.format_for_system_prompt("memory")
        if not memory_content:
            return ""
        return f"""## Persistent Memory

[Memory]:\n{memory_content}
"""

    def _build_skills_layer(
        self,
        override: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> str:
        """Layer 6: Skills Index (U2 — A1 fix + KTD-2 降级)

        Two paths:
          - Production path (override non-None, PhaseRunner node): ``override``
            (cannbot phase subset) + self-built section merged.
          - Default path (``/learn`` chat, override None): **self-built section only** —
            NO cannbot. Default-path full-cannbot rendering was the A1 token-budget
            bug (894 tok for all 16 cannbot in default path).

        Degradation (KTD-2 / F-4): self-built count > ``SELF_BUILT_DEGRADE_THRESHOLD``
        → filter by ``task_type`` subset; ``task_type`` missing → fall back to full
        (avoid empty). Threshold 12 ≈ triton phase (266 tok) + 12 self-built (480 tok)
        + 50 header/footer ≈ 796 tok ≤ 800.

        Args:
            override: cannbot phase-subset text from PhaseRunner node, or None
                (default path: ``/learn`` CLI sync to ``agent.run_conversation``).
            task_type: ``"develop"`` for PhaseRunner path; ``None`` for default path.

        Returns:
            Layer 6 text
        """
        from ascend_op_agent.orchestrator.cannbot_loader import render_skill_bundle_text
        from ascend_op_agent.skills.storage import SkillStorage

        # Load self-built skills via existing SkillStorage (already self-built-aware).
        storage = SkillStorage()
        self_built_skills = self._load_self_built_skills(storage)

        # Degradation (F-4 / KTD-2): filter by task_type if over threshold.
        if len(self_built_skills) > SELF_BUILT_DEGRADE_THRESHOLD and task_type:
            self_built_skills = [
                s for s in self_built_skills if self._self_built_task_type(s) == task_type
            ]
        # else: keep all (threshold not exceeded OR task_type missing → avoid empty)

        # Render self-built section via reused cannbot_loader function (A6 fix).
        # Override phase=None so render_skill_bundle_text emits "## Available Skills".
        self_built_section = (
            render_skill_bundle_text(self_built_skills) if self_built_skills else ""
        )

        if override is not None:
            # Production path: cannbot phase subset + self-built merge.
            if self_built_section:
                return override + "\n\n" + self_built_section
            return override

        # Default path (/learn chat, no phase context): self-built only.
        if not self_built_section:
            return (
                "## Available Skills\n\n"
                "(暂无自研 skill 沉淀 — use `/learn <topic>` to crystallize knowledge "
                "from GPU-Ascend operator development.)\n"
            )
        return self_built_section

    @staticmethod
    def _self_built_task_type(skill) -> Optional[str]:
        """Extract task_type from a skill. ``CannbotSkill`` stores its frontmatter
        dict under ``.frontmatter`` (not ``.metadata``); we set it from
        ``Skill.metadata`` in the thin adapter so the original nesting
        ``{"ascend_op_agent": {"task_type": ...}}`` survives.
        """
        fm = getattr(skill, "frontmatter", None) or {}
        aa = fm.get("ascend_op_agent", {}) if isinstance(fm, dict) else {}
        return aa.get("task_type")

    @staticmethod
    def _load_self_built_skills(storage) -> list:
        """Wrap SkillStorage-loaded self-built skills into CannbotSkill-compatible
        instances so ``render_skill_bundle_text`` (cannbot_loader) can render them.
        """
        from ascend_op_agent.orchestrator.cannbot_loader import CannbotSkill

        names = storage.list_skills()
        skills = []
        for name in names:
            loaded = storage.load_skill(name)
            if loaded is None:
                continue
            base_dir = Path(loaded.local_path) if loaded.local_path else Path(storage.skills_dir)
            skills.append(
                CannbotSkill(
                    name=loaded.name,
                    description=loaded.description,
                    body=loaded.content,
                    base_dir=base_dir,
                    frontmatter=loaded.metadata or {},
                )
            )
        return skills

    def _build_context_layer(self, workspace_path: str) -> str:
        """Layer 7: Context Files + Timestamp + Env"""
        parts = []

        # Context文件（优先级互斥模式）
        priority_files = [".hermes.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"]
        for filename in priority_files:
            filepath = os.path.join(workspace_path, filename)
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                # 安全扫描
                content = self._sanitize(content)
                parts.append(f"### {filename}\n{content}")
                break  # 只加载最高优先级文件

        # Timestamp
        from datetime import datetime

        parts.append(f"### Current Time\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Environment
        parts.append(f"### Working Directory\n{workspace_path}")

        return "\n\n".join(parts)

    def _sanitize(self, content: str) -> str:
        """安全扫描：防止提示词注入"""
        import re

        # 不可见字符
        invisible_patterns = [
            r"\x00",
            r"\u200b",
            r"\u202b",
            r"\ufeff",
        ]
        for pattern in invisible_patterns:
            content = re.sub(pattern, "", content)

        return content
