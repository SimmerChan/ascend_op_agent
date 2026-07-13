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

# PR-B U3 R5b: Hermes-style Layer 6 cap (R5b 路由命中 = 当前 task.type 命中
# + 同 task_type + 最近 5 轮 load,上限 10 — KTD-3 + Q2 决议沿用 hermes 默认).
# 超 cap 时按 (cannbot + self-built) 总数截断,优先保 cannbot phase subset
# (cannbot 是权威源,self-built 是 LLM 缓存加速).
HERMES_LAYER_LIMIT = 10
RECENT_LOADS_MAX = 5


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
        recent_loads: Optional[List[str]] = None,
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
            recent_loads: PR-B U3 R5b 路由命中 inputs —— 最近 N 轮
                ``skill_manage(action="load", ...)`` 加载过的 skill 名(去重保序,
                上限 RECENT_LOADS_MAX=5)。None 时只走 task_type 命中。

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
        # U3 R5b/R7: 把 recent_loads 一起传给 _build_skills_layer 做分组渲染
        # 和路由命中。
        layers.append(self._build_skills_layer(
            skills_layer_override, task_type, recent_loads,
        ))

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
        recent_loads: Optional[List[str]] = None,
    ) -> str:
        """Layer 6: Skills Index (PR-B U3 — R5b 路由命中 + R7 分组渲染)

        R5b 路由命中逻辑:
          1. cannbot phase subset (override, PhaseRunner 注入) ← 权威源
          2. self-built 同 task_type 命中(若 task_type 指定)
          3. + 最近 ``RECENT_LOADS_MAX`` 轮 ``skill_manage(action="load")``
             加载过的 self-built skill(去重保序) ← LLM 缓存加速
          4. cap 到 ``HERMES_LAYER_LIMIT`` (10, KTD-3 + Q2 决议;超过按
             (cannbot + self-built) 总数截断,优先保 cannbot phase subset,
             然后保最近 recent_loads,再按 task_type 命中剩余)

        R7 分组渲染:
          - cannbot 在前(标题 = "## Available Skills (phase=...)" 或
            "## Available Skills (cannbot reference)" 当 caller 没传 phase)
          - self-built 在后(标题 = "## Available Skills (self-built)")

        Both paths:
          - Production (override non-None, PhaseRunner node): cannbot +
            self-built merged.
          - Default (``/learn`` chat, override None, task_type None): self-built only
            (A1 fix — default path 不渲染 cannbot 全量).

        Args:
            override: cannbot phase-subset text from PhaseRunner node, or None
                (default path: ``/learn`` CLI sync to ``agent.run_conversation``).
            task_type: ``"develop"`` for PhaseRunner path; ``None`` for default path.
            recent_loads: 最近 N 轮 ``skill_manage(action="load", ...)`` 加载过的
                skill 名(去重保序,上限 RECENT_LOADS_MAX=5)。PR-B U3 R5b。

        Returns:
            Layer 6 text
        """
        from ascend_op_agent.orchestrator.cannbot_loader import render_skill_bundle_text
        from ascend_op_agent.skills.storage import SkillStorage

        storage = SkillStorage()
        self_built_skills = self._load_self_built_skills(storage)

        # ---- R5b 路由命中: 选 self-built 候选 ----
        candidates = self._select_skills_r5b(
            self_built_skills, task_type=task_type, recent_loads=recent_loads,
        )

        # ---- R5b cap: self-built 端到 HERMES_LAYER_LIMIT=10 ----
        if len(candidates) > HERMES_LAYER_LIMIT:
            candidates = candidates[:HERMES_LAYER_LIMIT]  # 截断(task_type 命中优先 + recent_loads 已 dedupe)

        # ---- R7 分组渲染: cannbot 前 + self-built 后 ----
        if override and candidates:
            # Production: 两段都存在,cannbot 在前 / self-built 在后
            self_built_section = self._render_self_built_section(candidates)
            return override + "\n\n" + self_built_section
        if override:
            # Production: 只有 cannbot phase subset
            return override
        if candidates:
            # Default path: 只有 self-built
            return self._render_self_built_section(candidates)
        # 两段都空 → fallback 提示(/learn 引导)
        return (
            "## Available Skills\n\n"
            "(暂无自研 skill 沉淀 — use `/learn <topic>` to crystallize knowledge "
            "from GPU-Ascend operator development.)\n"
        )

    def _select_skills_r5b(
        self,
        self_built_skills: list,
        task_type: Optional[str],
        recent_loads: Optional[List[str]],
    ) -> list:
        """PR-B U3 R5b: 选 self-built 候选(同 task_type + 最近 5 轮 load)。

        优先级: task_type 命中优先 → 加 recent_loads 补充(去重) → 后跟 R5b 旁路
        (无 task_type 时的全部; PR-A 兼容 — 旧 `task_type=None` 路径)。

        Returns:
            候选 self-built skill list(尚未 cap, cap 在上层 _build_skills_layer 做)
        """
        if not self_built_skills:
            return []

        seen: set[str] = set()
        candidates: list = []

        def _push(s) -> None:
            if s.name not in seen:
                seen.add(s.name)
                candidates.append(s)

        # 1. task_type 命中(若指定)
        if task_type:
            for s in self_built_skills:
                if self._self_built_task_type(s) == task_type:
                    _push(s)

        # 2. 最近 N 轮 load 补充(recent_loads 上限 RECENT_LOADS_MAX 保 caller 契约)
        if recent_loads:
            # Slicing to RECENT_LOADS_MAX 是 caller 责任;我们按 list 顺序遍历
            # 并去重已加入的。
            for name in (recent_loads or [])[:RECENT_LOADS_MAX]:
                if name in seen:
                    continue
                # 找 self-built 中名字匹配的(若有同名)
                for s in self_built_skills:
                    if s.name == name:
                        _push(s)
                        break

        if candidates:
            # 已经按 task_type + recent_loads 过滤了,直接返回
            return candidates

        # 3. 兜底: task_type=None + recent_loads=[] 时,无条件全保留(PR-A 行为)
        # 但仍走 HERMES_LAYER_LIMIT 在上层做(避免self_built > 12 时炸预算)。
        if not task_type and not recent_loads:
            return list(self_built_skills)

        # task_type/recent_loads 指定了但没匹配到任何 → 兜底返回全 self_built
        # (PR-A KTD-2: 避免 empty 反而让 LLM 无可用 skill)
        return list(self_built_skills)

    def _render_self_built_section(self, candidates: list) -> str:
        """PR-B U3 R7: self-built 段渲染 — 标题改为 "(self-built)" 区分 cannbot."""
        from ascend_op_agent.orchestrator.cannbot_loader import render_skill_bundle_text

        text = render_skill_bundle_text(candidates, phase=None)
        if not text:
            return ""
        # 替换第一处 "## Available Skills" 为带 self-built 后缀的标题。
        # (render_skill_bundle_text 在 phase=None 时正好输出 "## Available Skills"
        # 不带 phase 名)
        return text.replace(
            "## Available Skills", "## Available Skills (self-built)", 1,
        )

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
