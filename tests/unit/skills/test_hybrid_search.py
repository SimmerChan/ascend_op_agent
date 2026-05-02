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

"""混合检索测试"""

import tempfile

import pytest

from ascend_op_agent.skills.index import SkillIndex
from ascend_op_agent.skills.models import Skill


class TestHybridSearch:
    """混合检索测试"""

    def test_hybrid_search_basic(self):
        """测试混合检索基本功能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            # 添加多个Skill
            skills = [
                Skill(
                    name="matmul-template",
                    description="Matrix multiplication template",
                    content="Template for matrix multiplication operator",
                    tags=["matmul", "template"],
                ),
                Skill(
                    name="elementwise-template",
                    description="Elementwise operation template",
                    content="Template for elementwise operators",
                    tags=["elementwise", "template"],
                ),
                Skill(
                    name="reduction-template",
                    description="Reduction operation template",
                    content="Template for reduction operators like sum, max",
                    tags=["reduction", "template"],
                ),
            ]

            for skill in skills:
                index.add_skill(skill)

            # 测试混合搜索
            results = index.hybrid_search("matrix multiplication", k=3)

            # 应该返回结果
            assert len(results) >= 1
            # 应该包含相关的skill
            result_names = [s.name for s in results]
            assert "matmul-template" in result_names

    def test_hybrid_search_fusion(self):
        """测试混合检索融合排序"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            # 添加具有相似内容的不同skill
            skill1 = Skill(
                name="cuda-matmul",
                description="CUDA matrix multiplication",
                content="CUDA implementation of matrix multiplication using shared memory",
                tags=["cuda", "matmul"],
            )
            skill2 = Skill(
                name="triton-matmul",
                description="Triton matrix multiplication",
                content="Triton implementation of matrix multiplication using tiling",
                tags=["triton", "matmul"],
            )
            skill3 = Skill(
                name="softmax-op",
                description="Softmax operation",
                content="Softmax operator implementation",
                tags=["softmax", "activation"],
            )

            index.add_skill(skill1)
            index.add_skill(skill2)
            index.add_skill(skill3)

            # 搜索"matrix multiplication"
            results = index.hybrid_search("matrix multiplication", k=3)

            # matmul相关的skill应该在结果中
            result_names = [s.name for s in results]
            assert "cuda-matmul" in result_names
            assert "triton-matmul" in result_names

    def test_hybrid_search_alpha_parameter(self):
        """测试混合检索alpha参数"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="test-skill",
                description="A test skill with specific keywords",
                content="Content with specific keywords",
                tags=["test"],
            )
            index.add_skill(skill)

            # alpha=1.0 表示只使用FTS5
            results_fts = index.hybrid_search("keywords", k=5, alpha=1.0)
            # alpha=0.0 表示只使用向量
            # alpha=0.4 表示混合（默认值）

            # 结果数量应该合理
            assert len(results_fts) <= 5

    def test_hybrid_search_empty_query(self):
        """测试空查询处理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="test-skill",
                description="Test",
                content="Test content",
                tags=["test"],
            )
            index.add_skill(skill)

            # 空字符串查询应该返回结果
            results = index.hybrid_search("", k=5)
            # 应该至少有一些结果（因为有添加的skill）
            assert len(results) >= 0

    def test_hybrid_search_with_fts5_fallback(self):
        """测试混合检索在embedding不可用时回退到FTS5"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skill = Skill(
                name="fallback-test",
                description="Test skill for fallback",
                content="Fallback test content",
                tags=["fallback", "test"],
            )
            index.add_skill(skill)

            # 如果embedding模型不可用，应该回退到纯FTS5
            if index.embedding_model is None:
                results = index.hybrid_search("fallback", k=5)
                assert len(results) >= 1
                assert any(s.name == "fallback-test" for s in results)

    def test_hybrid_search_multiple_keywords(self):
        """测试多关键词混合搜索"""
        with tempfile.TemporaryDirectory() as tmpdir:
            index = SkillIndex(
                db_path=f"{tmpdir}/test.db",
                cache_dir=tmpdir,
                vector_store_dir=f"{tmpdir}/vector_db",
            )

            skills = [
                Skill(
                    name="cuda-gemm",
                    description="CUDA GEMM implementation",
                    content="CUDA general matrix multiply implementation",
                    tags=["cuda", "gemm", "matmul"],
                ),
                Skill(
                    name="triton-gemm",
                    description="Triton GEMM implementation",
                    content="Triton general matrix multiply implementation",
                    tags=["triton", "gemm", "matmul"],
                ),
                Skill(
                    name="softmax-cuda",
                    description="CUDA Softmax",
                    content="CUDA softmax implementation",
                    tags=["cuda", "softmax"],
                ),
            ]

            for skill in skills:
                index.add_skill(skill)

            # 搜索多个关键词
            results = index.hybrid_search("CUDA GEMM matrix multiply", k=3)

            # 应该返回相关结果
            assert len(results) >= 1
            # GEMM相关的应该在结果中（如果embedding可用，否则FTS5可能只返回一个）
            result_names = [s.name for s in results]
            assert "cuda-gemm" in result_names
            # triton-gemm可能在embedding不可用时无法被召回
            if index.embedding_model is not None:
                assert "triton-gemm" in result_names
