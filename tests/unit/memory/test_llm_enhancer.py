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

"""LLM Enhancer 单元测试"""

import pytest

from ascend_op_agent.memory.llm_enhancer import (
    ExperienceHint,
    LlmEnhancer,
    LlmEnhancerConfig,
    LlmEnhancerFactory,
)


class MockLLMClient:
    """模拟 LLMClient"""

    def __init__(self, response: str = "Mock LLM response"):
        self.response = response
        self.call_count = 0

    def call(self, system_prompt: str, conversation_history: list) -> str:
        """模拟 LLM 调用"""
        self.call_count += 1
        return self.response


class MockSkill:
    """模拟 Skill 对象"""

    def __init__(self, name: str, description: str, score: float = 0.0):
        self.name = name
        self.description = description
        self.score = score


class TestLlmEnhancerConfig:
    """LlmEnhancerConfig 测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = LlmEnhancerConfig()

        assert config.enabled is False
        assert config.trigger_threshold == 0.6
        assert config.model is None

    def test_custom_config(self):
        """测试自定义配置"""
        config = LlmEnhancerConfig(
            enabled=True,
            trigger_threshold=0.7,
            model="gpt-4o",
        )

        assert config.enabled is True
        assert config.trigger_threshold == 0.7
        assert config.model == "gpt-4o"


class TestLlmEnhancer:
    """LlmEnhancer 测试"""

    def test_disabled_by_default(self):
        """测试默认禁用"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancer(llm_client=mock_client)

        assert enhancer.is_enabled() is False
        assert enhancer.should_enhance([]) is False

    def test_enabled_with_config(self):
        """测试启用配置"""
        mock_client = MockLLMClient()
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        assert enhancer.is_enabled() is True

    def test_should_enhance_empty_results(self):
        """测试空结果时触发增强"""
        mock_client = MockLLMClient()
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        assert enhancer.should_enhance([]) is True

    def test_should_enhance_low_score(self):
        """测试低分结果时触发增强"""
        mock_client = MockLLMClient()
        config = LlmEnhancerConfig(enabled=True, trigger_threshold=0.6)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        # 最高分低于阈值
        results = [MockSkill("test", "desc", score=0.4)]
        assert enhancer.should_enhance(results) is True

    def test_should_not_enhance_high_score(self):
        """测试高分结果时不触发增强"""
        mock_client = MockLLMClient()
        config = LlmEnhancerConfig(enabled=True, trigger_threshold=0.6)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        # 最高分高于阈值
        results = [MockSkill("test", "desc", score=0.8)]
        assert enhancer.should_enhance(results) is False

    def test_should_not_enhance_when_disabled(self):
        """测试禁用时不触发增强"""
        mock_client = MockLLMClient()
        config = LlmEnhancerConfig(enabled=False)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        # 即使分数低也不触发
        results = [MockSkill("test", "desc", score=0.3)]
        assert enhancer.should_enhance(results) is False

    def test_enhance_returns_empty_when_disabled(self):
        """测试禁用时增强返回空"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancer(llm_client=mock_client)

        import asyncio

        hints = asyncio.run(enhancer.enhance("test query", []))

        assert hints == []

    def test_enhance_parses_hints(self):
        """测试增强解析提示"""
        mock_response = """HINT: Consider using attention patterns from NLP operators | REASON: Similar matrix operations | CONFIDENCE: 0.8
HINT: Check GEMM implementation in similar operators | REASON: Related operation type | CONFIDENCE: 0.7"""

        mock_client = MockLLMClient(response=mock_response)
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        import asyncio

        hints = asyncio.run(enhancer.enhance("matmul development", []))

        assert len(hints) == 2
        assert hints[0].content == "Consider using attention patterns from NLP operators"
        assert hints[0].reason == "Similar matrix operations"
        assert hints[0].confidence == 0.8
        assert hints[0].source == "cross_type"

    def test_enhance_fallback_to_direct_response(self):
        """测试增强回退到直接响应"""
        mock_response = "Some general advice about operator development"
        mock_client = MockLLMClient(response=mock_response)
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        import asyncio

        hints = asyncio.run(enhancer.enhance("test query", []))

        # 应该有一个回退提示
        assert len(hints) == 1
        assert hints[0].content == mock_response[:200]

    def test_disambiguate(self):
        """测试语义消歧"""
        mock_response = """TERM: gemm
MEANING: General Matrix Multiply operation
CONFIDENCE: 0.9"""
        mock_client = MockLLMClient(response=mock_response)
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        import asyncio

        result = asyncio.run(enhancer.disambiguate("gemm", "matrix multiplication"))

        assert result["term"] == "gemm"
        assert result["meaning"] == "General Matrix Multiply operation"
        assert result["confidence"] == 0.9

    def test_disambiguate_disabled(self):
        """测试禁用消歧"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancer(llm_client=mock_client)

        import asyncio

        result = asyncio.run(enhancer.disambiguate("gemm", "matrix multiplication"))

        # 禁用时返回原始术语
        assert result["term"] == "gemm"
        assert result["meaning"] == "gemm"
        assert result["confidence"] == 0.0

    def test_summarize_sessions(self):
        """测试会话总结"""
        mock_response = "Common pattern: Memory allocation issues in large tensor operations"
        mock_client = MockLLMClient(response=mock_response)
        config = LlmEnhancerConfig(enabled=True)
        enhancer = LlmEnhancer(llm_client=mock_client, config=config)

        sessions = [
            {
                "turns": [
                    {"role": "user", "content": "Issue with large tensor"},
                    {"role": "assistant", "content": "Check memory allocation"},
                ]
            },
        ]

        import asyncio

        summary = asyncio.run(enhancer.summarize_sessions(sessions))

        assert "Memory allocation" in summary

    def test_summarize_sessions_disabled(self):
        """测试禁用时会话总结返回空"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancer(llm_client=mock_client)

        import asyncio

        summary = asyncio.run(enhancer.summarize_sessions([]))

        assert summary == ""


class TestLlmEnhancerFactory:
    """LlmEnhancerFactory 测试"""

    def test_create_with_defaults(self):
        """测试使用默认配置创建"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancerFactory.create(mock_client)

        assert enhancer.is_enabled() is False
        assert enhancer.config.trigger_threshold == 0.6

    def test_create_with_custom_config(self):
        """测试使用自定义配置创建"""
        mock_client = MockLLMClient()
        enhancer = LlmEnhancerFactory.create(
            llm_client=mock_client,
            enabled=True,
            trigger_threshold=0.7,
            model="gpt-4o",
        )

        assert enhancer.is_enabled() is True
        assert enhancer.config.trigger_threshold == 0.7
        assert enhancer.config.model == "gpt-4o"


class TestExperienceHint:
    """ExperienceHint 测试"""

    def test_creation(self):
        """测试创建"""
        hint = ExperienceHint(
            content="Test hint",
            reason="Test reason",
            confidence=0.8,
            source="cross_type",
        )

        assert hint.content == "Test hint"
        assert hint.reason == "Test reason"
        assert hint.confidence == 0.8
        assert hint.source == "cross_type"
