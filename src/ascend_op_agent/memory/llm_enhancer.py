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

"""LLM Enhancer - 通过 LLM 增强跨会话知识召回

使用 LLM 增强语义检索能力：
1. 跨类型推荐: "开发 MatMul 时，提示 Attention 算子有类似 Pattern"
2. 复杂推理: "分析多个失败会话，提取共性根因"
3. 语义消歧: "判断 'gemm' 指的是矩阵乘还是通用矩阵运算"
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class LlmEnhancerConfig:
    """LLM 增强器配置"""

    enabled: bool = False  # 是否启用 LLM 增强
    trigger_threshold: float = 0.6  # 基础检索得分低于此阈值时触发
    model: Optional[str] = None  # 可选，覆盖 LLMClient 默认模型


@dataclass
class ExperienceHint:
    """LLM 增强的经验提示"""

    content: str  # 提示内容
    reason: str  # 生成原因
    confidence: float  # 置信度 (0-1)
    source: str  # 来源: "cross_type", "session_analysis", "semantic_disambiguation"


class LlmEnhancer:
    """LLM 增强器

    通过 LLM 增强跨会话知识召回能力。
    直接使用 Agent 配置的 LLMClient，无需额外的 MCP 接口。
    """

    def __init__(
        self,
        llm_client: Any,
        config: Optional[LlmEnhancerConfig] = None,
    ):
        """
        Args:
            llm_client: LLMClient 实例
            config: LLM 增强器配置
        """
        self.llm_client = llm_client
        self.config = config or LlmEnhancerConfig()

    def is_enabled(self) -> bool:
        """检查是否启用 LLM 增强"""
        return self.config.enabled

    def should_enhance(self, base_results: list[Any], min_score: float = 0.6) -> bool:
        """检查是否应该触发 LLM 增强

        Args:
            base_results: 基础检索结果
            min_score: 最低分数阈值

        Returns:
            是否应该触发 LLM 增强
        """
        if not self.is_enabled():
            return False

        # 如果基础检索结果为空或得分都很低，触发增强
        if not base_results:
            return True

        # 检查最高得分是否低于阈值
        if hasattr(base_results[0], "score"):
            return base_results[0].score < self.config.trigger_threshold

        return False

    async def enhance(
        self,
        query: str,
        base_results: list[Any],
        op_type: Optional[str] = None,
    ) -> list[ExperienceHint]:
        """增强检索结果

        Args:
            query: 检索 query
            base_results: 基础检索结果 (Skill 列表)
            op_type: 可选的算子类型过滤

        Returns:
            LLM 增强后的经验提示列表
        """
        if not self.is_enabled():
            return []

        try:
            # 构建提示
            prompt = self._build_enhance_prompt(query, base_results, op_type)

            # 调用 LLM
            response = await self._call_llm(prompt)

            # 解析响应
            hints = self._parse_enhance_response(response, query)

            logger.info(f"LLM enhancer returned {len(hints)} hints for query: {query}")
            return hints

        except Exception as e:
            logger.warning(f"LLM enhancement failed: {e}")
            return []

    async def summarize_sessions(
        self,
        sessions: list[dict[str, Any]],
    ) -> str:
        """总结多个会话，提取共性知识

        Args:
            sessions: 会话列表，每个会话包含 role/content 对

        Returns:
            总结的共性知识
        """
        if not self.is_enabled():
            return ""

        try:
            prompt = self._build_session_summary_prompt(sessions)
            response = await self._call_llm(prompt)
            return response

        except Exception as e:
            logger.warning(f"Session summarization failed: {e}")
            return ""

    async def disambiguate(
        self,
        term: str,
        context: str,
    ) -> dict[str, Any]:
        """语义消歧

        Args:
            term: 需要消歧的术语
            context: 上下文描述

        Returns:
            消歧结果，包含术语含义和置信度
        """
        if not self.is_enabled():
            return {"term": term, "meaning": term, "confidence": 0.0}

        try:
            prompt = self._build_disambiguation_prompt(term, context)
            response = await self._call_llm(prompt)
            return self._parse_disambiguation_response(response, term)

        except Exception as e:
            logger.warning(f"Semantic disambiguation failed: {e}")
            return {"term": term, "meaning": term, "confidence": 0.0}

    def _build_enhance_prompt(
        self,
        query: str,
        base_results: list[Any],
        op_type: Optional[str] = None,
    ) -> str:
        """构建增强提示"""
        prompt_parts = [
            "You are a helpful assistant that provides additional context for operator development queries.",
            "",
            f"Current query: {query}",
        ]

        if op_type:
            prompt_parts.append(f"Current operator type: {op_type}")

        if base_results:
            prompt_parts.append("")
            prompt_parts.append("Base search results (may be incomplete or low-scored):")
            for i, result in enumerate(base_results[:5], 1):
                name = getattr(result, "name", "unknown")
                desc = getattr(result, "description", "")
                prompt_parts.append(f"{i}. {name}: {desc[:100]}")
        else:
            prompt_parts.append("")
            prompt_parts.append("No base search results found.")

        prompt_parts.append("")
        prompt_parts.append(
            "Based on the query, provide 1-3 relevant hints that might help with operator development. "
            "Focus on: related operator patterns, similar implementation approaches, "
            "or relevant lessons learned from other operator development experiences. "
            "Format each hint as: HINT: <hint text> | REASON: <why this is relevant> | CONFIDENCE: <0.0-1.0>"
        )

        return "\n".join(prompt_parts)

    def _build_session_summary_prompt(self, sessions: list[dict[str, Any]]) -> str:
        """构建会话总结提示"""
        prompt_parts = [
            "You are a helpful assistant that analyzes operator development sessions.",
            "",
            "Analyze the following conversation sessions and extract common patterns, "
            "shared learnings, and recurring issues:",
            "",
        ]

        for i, session in enumerate(sessions[:5], 1):
            prompt_parts.append(f"Session {i}:")
            if isinstance(session, dict):
                for turn in session.get("turns", [])[:10]:
                    role = turn.get("role", "unknown")
                    content = turn.get("content", "")[:200]
                    prompt_parts.append(f"  {role}: {content}")
            prompt_parts.append("")

        prompt_parts.append("Provide a concise summary of the common themes and learnings.")

        return "\n".join(prompt_parts)

    def _build_disambiguation_prompt(self, term: str, context: str) -> str:
        """构建消歧提示"""
        return f"""You are a helpful assistant that disambiguates technical terms.

Term to disambiguate: {term}

Context: {context}

What does this term likely refer to in the context of operator development?
Is it:
1. A matrix multiplication operation (GEMM)?
2. A general matrix operation?
3. Something else?

Respond in format:
TERM: {term}
MEANING: <your interpretation>
CONFIDENCE: <0.0-1.0>
"""

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM

        Args:
            prompt: 提示文本

        Returns:
            LLM 响应
        """
        # 构建对话历史
        conversation_history = [{"role": "user", "content": prompt}]

        try:
            # 使用 LLMClient 调用
            response = self.llm_client.call(
                system_prompt="You are a helpful assistant specialized in operator development for Ascend hardware.",
                conversation_history=conversation_history,
            )
            return response
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")
            raise

    def _parse_enhance_response(
        self,
        response: str,
        query: str,
    ) -> list[ExperienceHint]:
        """解析 LLM 增强响应"""
        hints = []

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("HINT:"):
                # 解析 HINT: <content> | REASON: <reason> | CONFIDENCE: <0.0-1.0>
                try:
                    parts = line.split("|")
                    hint_text = parts[0].replace("HINT:", "").strip()
                    reason = ""
                    confidence = 0.5

                    for part in parts[1:]:
                        part = part.strip()
                        if part.startswith("REASON:"):
                            reason = part.replace("REASON:", "").strip()
                        elif part.startswith("CONFIDENCE:"):
                            try:
                                confidence = float(part.replace("CONFIDENCE:", "").strip())
                            except ValueError:
                                confidence = 0.5

                    if hint_text:
                        hints.append(
                            ExperienceHint(
                                content=hint_text,
                                reason=reason or "LLM suggested",
                                confidence=confidence,
                                source="cross_type",
                            )
                        )
                except Exception as e:
                    logger.debug(f"Failed to parse hint line: {line}, error: {e}")

        # 如果没有解析出提示，创建默认提示
        if not hints and response.strip():
            hints.append(
                ExperienceHint(
                    content=response.strip()[:200],
                    reason="Direct LLM response",
                    confidence=0.5,
                    source="cross_type",
                )
            )

        return hints

    def _parse_disambiguation_response(
        self,
        response: str,
        term: str,
    ) -> dict[str, Any]:
        """解析消歧响应"""
        meaning = term
        confidence = 0.5

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("MEANING:"):
                meaning = line.replace("MEANING:", "").strip()
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = float(line.replace("CONFIDENCE:", "").strip())
                except ValueError:
                    confidence = 0.5

        return {
            "term": term,
            "meaning": meaning,
            "confidence": confidence,
        }


class LlmEnhancerFactory:
    """LlmEnhancer 工厂类"""

    @staticmethod
    def create(
        llm_client: Any,
        enabled: bool = False,
        trigger_threshold: float = 0.6,
        model: Optional[str] = None,
    ) -> LlmEnhancer:
        """创建 LlmEnhancer 实例

        Args:
            llm_client: LLMClient 实例
            enabled: 是否启用
            trigger_threshold: 触发阈值
            model: 可选的模型名称

        Returns:
            LlmEnhancer 实例
        """
        config = LlmEnhancerConfig(
            enabled=enabled,
            trigger_threshold=trigger_threshold,
            model=model,
        )
        return LlmEnhancer(llm_client=llm_client, config=config)
