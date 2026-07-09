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
from typing import Optional

from ascend_op_agent.agent.memory import MemoryStore


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
    ) -> str:
        """构建完整的系统Prompt（7层组装）

        Args:
            workspace_path: 工作区路径
            memory_store: 记忆存储
            skills_layer_override: 可选,注入该阶段 cannbot skill 包替换默认 Layer 6。
                由编排器 LLM 节点调用时传入(hybrid 集成的编排层入口);

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
        if skills_layer_override is not None:
            layers.append(skills_layer_override)
        else:
            layers.append(self._build_skills_layer())

        # Layer 7: Context Files + Timestamp + Env
        layers.append(self._build_context_layer(workspace_path))

        return "\n\n".join(filter(None, layers))

    def _build_identity_layer(self) -> str:
        """Layer 1: Agent Identity"""
        if self._soul_path.exists():
            with open(self._soul_path, 'r', encoding='utf-8') as f:
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

    def _build_skills_layer(self) -> str:
        """Layer 6: Skills Index"""
        return """## Available Skills

Skills存储在 ~/.ascend_op_agent/skills/ 目录
每个Skill包含:
- SKILL.md: Skill定义和描述
- templates/: 代码模板
- references/: 参考资料

使用skill_ops工具搜索和加载相关Skill。
"""

    def _build_context_layer(self, workspace_path: str) -> str:
        """Layer 7: Context Files + Timestamp + Env"""
        parts = []

        # Context文件（优先级互斥模式）
        priority_files = ['.hermes.md', 'AGENTS.md', 'CLAUDE.md', '.cursorrules']
        for filename in priority_files:
            filepath = os.path.join(workspace_path, filename)
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
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
            r'\x00', r'\u200b', r'\u202b', r'\ufeff',
        ]
        for pattern in invisible_patterns:
            content = re.sub(pattern, '', content)

        return content


# ---- U7 意图分类器 prompt(KTD2 hybrid free-text 分类) ----
# 独立于 PromptBuilder 的 7 层 system prompt —— 这是分类器专用的单次 LLM 调用 prompt
# 模板。task_router.intent_classifier 经 ``build_classifier_prompt`` 引用本节(plan U7:
# "prompt_builder.py 扩,加 classifier prompt section")。

_CLASSIFIER_PROMPT_TEMPLATE = """你是任务意图分类器。把用户输入分到恰好一个类别,只输出纯 JSON。

类别:
- on-task:推进当前 active task(继续算子开发 / 跑编译 / 接着上一步)
- off-task:与任务无关的闲聊 / 问候 / 通用问答
- new-task:要开新任务(如"帮我迁这个模型"→ migrate;"分析这个 op"→ analyze)
- progress-query:查进展(如"现在到哪了 / 进度 / 状态")

{active_ctx}

用户输入:
{user_input}

输出格式(纯 JSON,无 markdown / 无多余文字):
{{"label": "<on-task|off-task|new-task|progress-query>", "confidence": 0.0-1.0, "suggested_task_type": "migrate|analyze|optimize|develop|null"}}
"""


def build_classifier_prompt(user_input: str, active_task_context=None) -> str:
    """组装 U7 意图分类器 prompt(task_router.intent_classifier 调用)。

    Args:
        user_input: 用户输入文本。
        active_task_context: ``ActiveTaskContext`` 或 None;非空时附 active task 描述
            帮助 LLM 判断 on-task 相关性。

    Returns:
        分类器 prompt 字符串。
    """
    if active_task_context is not None:
        import json as _json

        active_ctx = (
            "当前 active task 上下文:\n"
            f"  task_id={active_task_context.task_id} "
            f"type={active_task_context.task_type or '未知'}\n"
            f"  object={_json.dumps(active_task_context.object_payload, ensure_ascii=False)}"
        )
    else:
        active_ctx = "当前无 active task(用户可能要开新任务或闲聊)。"
    return _CLASSIFIER_PROMPT_TEMPLATE.format(
        active_ctx=active_ctx, user_input=user_input
    )
