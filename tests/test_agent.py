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

"""Agent核心模块测试"""

import tempfile
from pathlib import Path

import pytest

from ascend_op_agent.agent import (
    AIAgent,
    ContextEngine,
    MemoryStore,
    PromptBuilder,
    Tool,
    ToolRegistry,
    tool_registry,
)


class TestTool:
    """Tool类测试"""

    def test_tool_creation(self):
        """测试Tool创建"""
        def dummy_func(x: str, y: int) -> str:
            return f"{x} {y}"

        tool = Tool(
            name="test_tool",
            description="测试工具",
            func=dummy_func,
        )
        assert tool.name == "test_tool"
        assert tool.description == "测试工具"

    def test_tool_execution(self):
        """测试Tool执行"""
        def add(a: int, b: int) -> int:
            return a + b

        tool = Tool(name="add", description="加法", func=add)
        result = tool.execute(a=1, b=2)
        assert result == 3

    def test_to_openai_format(self):
        """测试OpenAI格式转换"""
        def dummy(x: str) -> str:
            return x

        tool = Tool(name="dummy", description="测试", func=dummy)
        format_data = tool.to_openai_format()
        assert format_data["type"] == "function"
        assert format_data["function"]["name"] == "dummy"


class TestToolRegistry:
    """ToolRegistry测试"""

    def test_register_tool(self):
        """测试工具注册"""
        registry = ToolRegistry()

        def my_tool(arg: str) -> str:
            return arg

        registry.register(name="my_tool", description="我的工具", func=my_tool)
        assert registry.get_tool("my_tool") is not None
        assert "my_tool" in registry.list_tools()

    def test_get_nonexistent_tool(self):
        """测试获取不存在的工具"""
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None

    def test_decorator_registration(self):
        """测试装饰器注册"""
        # 创建新的registry避免冲突
        from ascend_op_agent.agent.tool_registry import tool

        @tool(name="decorator_tool", description="装饰器测试")
        def decorator_tool(x: int) -> int:
            return x * 2

        # 使用全局registry检查
        from ascend_op_agent.agent.tool_registry import tool_registry
        assert tool_registry.get_tool("decorator_tool") is not None


class TestMemoryStore:
    """MemoryStore测试"""

    def test_add_and_get(self):
        """测试添加和获取记忆"""
        store = MemoryStore()
        store.add("memory", "这是一个测试记忆")
        assert "这是一个测试记忆" in store.get("memory")

    def test_format_for_system_prompt(self):
        """测试格式化系统Prompt"""
        store = MemoryStore()
        store.add("memory", "记忆1")
        store.add("memory", "记忆2")
        formatted = store.format_for_system_prompt("memory")
        assert "MEMORY" in formatted
        assert "记忆1" in formatted
        assert "记忆2" in formatted

    def test_freeze_and_restore_snapshot(self):
        """测试快照冻结和恢复"""
        store = MemoryStore()
        store.add("memory", "原始记忆")

        # 冻结
        store.freeze_snapshot()

        # 添加新记忆
        store.add("memory", "新记忆")

        # 恢复
        store.restore_snapshot()

        # 验证新记忆被移除
        assert "新记忆" not in store.get("memory")
        assert "原始记忆" in store.get("memory")

    def test_clear(self):
        """测试清除记忆"""
        store = MemoryStore()
        store.add("memory", "记忆1")
        store.add("user", "偏好1")

        store.clear("memory")
        assert len(store.get("memory")) == 0
        assert len(store.get("user")) == 1

        store.clear()
        assert len(store.get("memory")) == 0
        assert len(store.get("user")) == 0


class TestContextEngine:
    """ContextEngine测试"""

    def test_build_context_prompt_no_file(self):
        """测试无上下文文件时"""
        engine = ContextEngine()
        result = engine.build_context_prompt("/nonexistent/path")
        assert result == ""

    def test_build_context_prompt_with_file(self):
        """测试有上下文文件时"""
        engine = ContextEngine()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建CLAUDE.md文件
            claude_md = Path(tmpdir) / "CLAUDE.md"
            claude_md.write_text("# Test Context\n\nThis is a test.", encoding="utf-8")

            result = engine.build_context_prompt(tmpdir)
            assert "Test Context" in result
            assert "This is a test" in result

    def test_priority_mutual_exclusion(self):
        """测试优先级互斥（只加载最高优先级文件）"""
        engine = ContextEngine()

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建多个上下文文件
            (Path(tmpdir) / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")
            (Path(tmpdir) / "CLAUDE.md").write_text("# CLAUDE", encoding="utf-8")

            result = engine.build_context_prompt(tmpdir)
            # 应该只加载AGENTS.md（更高优先级）
            assert "AGENTS" in result
            assert "CLAUDE" not in result

    def test_sanitize(self):
        """测试安全扫描"""
        engine = ContextEngine()

        # 注入不可见字符
        malicious = "normal\u200b\u202b\ufefftext"
        sanitized = engine._sanitize(malicious)
        assert "\u200b" not in sanitized
        assert "\u202b" not in sanitized
        assert "\ufeff" not in sanitized
        assert "normaltext" in sanitized


class TestPromptBuilder:
    """PromptBuilder测试"""

    def test_build_system_prompt(self):
        """测试构建系统Prompt"""
        builder = PromptBuilder()
        memory_store = MemoryStore()

        with tempfile.TemporaryDirectory() as tmpdir:
            prompt = builder.build_system_prompt(
                workspace_path=tmpdir,
                memory_store=memory_store,
            )

            # 验证7层Prompt主要部分存在
            assert "Hermes Help Guidance" in prompt
            assert "Tool Usage" in prompt
            assert "Ascend Op Agent" in prompt
            # 检查工具调用格式存在于prompt中
            assert len(prompt) > 500  # prompt应该足够长

    def test_identity_layer(self):
        """测试Identity层"""
        builder = PromptBuilder()
        # SOUL.md应该被加载
        assert builder._soul_path.exists()

    def test_memory_layer(self):
        """测试记忆层"""
        builder = PromptBuilder()
        memory_store = MemoryStore()
        memory_store.add("memory", "测试记忆")

        layer = builder._build_memory_layer(memory_store)
        assert "测试记忆" in layer


class TestAIAgent:
    """AIAgent测试"""

    def test_agent_initialization(self):
        """测试Agent初始化"""
        from ascend_op_agent.config import Config
        from ascend_op_agent.agent.tool_registry import tool_registry

        config = Config()
        registry = tool_registry  # 使用全局注册表（含内置工具）
        prompt_builder = PromptBuilder()
        context_engine = ContextEngine()
        memory_store = MemoryStore()

        agent = AIAgent(
            config=config,
            tool_registry=registry,
            prompt_builder=prompt_builder,
            context_engine=context_engine,
            memory_store=memory_store,
        )

        assert agent.config is not None
        assert agent.tool_registry is not None
        assert len(agent.tools) > 0  # 内置工具应该已注册

    def test_reset_conversation(self):
        """测试重置对话"""
        from ascend_op_agent.config import Config

        agent = AIAgent(
            config=Config(),
            tool_registry=ToolRegistry(),
            prompt_builder=PromptBuilder(),
            context_engine=ContextEngine(),
            memory_store=MemoryStore(),
        )

        agent._conversation_history = [{"role": "user", "content": "test"}]
        agent.reset_conversation()
        assert len(agent._conversation_history) == 0
