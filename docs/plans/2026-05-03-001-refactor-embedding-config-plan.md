---
title: refactor: 将 Embedding 模型和存储路径配置化
type: refactor
status: active
date: 2026-05-03
---

# refactor: 将 Embedding 模型和存储路径配置化

## Overview

将目前硬编码的 embedding 模型名称、维度以及 ChromaDB 持久化路径改为可通过配置文件修改，实现配置与代码分离。

## Problem Frame

当前 embedding 模型选择和向量存储路径存在 3 处硬编码：
1. `skills/index.py` 第52-53行：`EMBEDDING_MODEL` 和 `EMBEDDING_DIM`
2. `vector_store.py` 第34-35行：同样的常量重复定义
3. `episodic_memory.py` 第198行和第252行：硬编码模型名称
4. `vector_store.py` 第57行：`~/.ascend_op_agent/vector_db` 路径硬编码

这导致：
- 无法在不修改代码的情况下切换不同的 embedding 模型
- 无法通过配置指定向量数据库的存储位置
- 模型配置分散在多个文件中，存在不一致风险

## Requirements Trace

- R1. embedding 模型名称可通过配置文件修改
- R2. embedding 向量维度可配置（与模型匹配）
- R3. ChromaDB 持久化路径可通过配置文件修改
- R4. 配置支持 `${ENV_VAR}` 环境变量引用格式
- R5. 向后兼容：不提供配置时使用当前默认值

## Scope Boundaries

- **不涉及**: LLM 模型配置（已有 `LLMConfig`）
- **不涉及**: Skill 仓库 URL 配置（已有 `skill_repositories`）
- **不涉及**: MCP 服务器配置

## Context & Research

### Relevant Code and Patterns

| 文件 | 当前状态 | 模式 |
|------|---------|------|
| `config.py` | 有 `LLMConfig`，无 embedding 配置 | 参考 `LLMConfig` 的 Pydantic 模型模式 |
| `skills/index.py` | 第52-53行硬编码 `EMBEDDING_MODEL` | 惰性加载模型，单例缓存 |
| `episodic_memory.py` | 第198、252行硬编码模型名称 | 每次调用重新创建模型实例 |
| `vector_store.py` | 第34-35行重复定义，第57行硬编码路径 | `__init__` 接受 `persist_dir` 参数 |

### 配置系统现有模式

- `config.py` 使用 Pydantic `BaseModel` 定义配置类
- 支持 `${ENV_VAR}` 环境变量引用解析（`_resolve_env_vars` 方法）
- `LocalConfig` 定义了 `skills_path` 等本地路径配置

## Key Technical Decisions

- **Decision 1**: 新增 `EmbeddingConfig` 和 `VectorStoreConfig` 两个配置类
  - `Rationale`: 向量存储路径与 embedding 配置在语义上相关但职责不同，分开更清晰
- **Decision 2**: embedding 配置仅支持模型名称，维度由模型决定
  - `Rationale`: `all-MiniLM-L6-v3` 输出固定 384 维，动态计算维度增加复杂度
- **Decision 3**: `VectorStore` 的 `persist_dir` 默认值从配置读取，而非硬编码
  - `Rationale`: 与 `LocalConfig.skills_path` 保持一致的模式
- **Decision 4**: 保留 `EMBEDDING_DIM` 常量用于零向量 fallback
  - `Rationale`: fallback 时需要已知维度，配置化后仍需此常量

## Open Questions

### Resolved During Planning

- **问**: `episodic_memory.py` 每次创建新模型实例的问题是否一并修复？
  - **答**: 不在本次范围内，本次仅处理配置化，实例缓存优化可单独进行

### Deferred to Implementation

- **问**: 是否需要验证配置的模型名称是否有效？
  - **答**: 首次加载时由 `sentence_transformers` 库验证，失败时记录 warning

## Implementation Units

- [ ] **Unit 1: 添加 EmbeddingConfig 和 VectorStoreConfig 到 config.py**

**Goal:** 定义 embedding 模型和向量存储的配置结构

**Requirements:** R1, R2, R3, R4, R5

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/config.py`

**Approach:**
- 新增 `EmbeddingConfig` Pydantic 模型，包含 `model` 和 `dimension` 字段
- 新增 `VectorStoreConfig` Pydantic 模型，包含 `persist_dir` 字段
- 在 `Config` 类中添加这两个配置域
- 保持向后兼容：默认值与当前硬编码值一致

**Patterns to follow:**
- 参考 `LLMConfig` 的字段定义风格
- 参考 `LocalConfig` 的路径配置风格

**Test scenarios:**
- 配置对象可正确序列化/反序列化
- 环境变量引用 `${EMBEDDING_MODEL}` 可被正确解析
- 默认值与现有硬编码值一致

**Verification:**
- 运行 `python -c "from ascend_op_agent.config import Config; print(Config().embedding.model)"` 输出 `"sentence-transformers/all-MiniLM-L6-v3"`
- 运行 `python -c "from ascend_op_agent.config import Config; print(Config().vector_store.persist_dir)"` 输出包含 `vector_db` 的路径

---

- [ ] **Unit 2: 更新 config.yaml.example 添加 embedding 和 vector_store 配置节**

**Goal:** 提供配置文件的完整示例

**Requirements:** R3, R4

**Dependencies:** Unit 1

**Files:**
- Modify: `config.yaml.example`

**Approach:**
- 在文件末尾添加 `embedding` 和 `vector_store` 配置节
- 使用与环境变量引用组合的配置示例

**Patterns to follow:**
- 参照现有配置节的注释风格
- 使用 `# 可选` 标注非强制字段

**Test scenarios:**
- 配置文件可被 `Config.from_file()` 正确加载

**Verification:**
- `Config.from_file("config.yaml.example")` 不抛出异常

---

- [ ] **Unit 3: 修改 skills/index.py 使用配置化的 embedding**

**Goal:** 移除硬编码常量，改用配置注入

**Requirements:** R1, R2, R5

**Dependencies:** Unit 1

**Files:**
- Modify: `src/ascend_op_agent/skills/index.py`

**Approach:**
- `__init__` 新增 `embedding_model_name` 和 `embedding_dimension` 参数
- 从 `Config` 读取默认值
- 移除模块级 `EMBEDDING_MODEL` 和 `EMBEDDING_DIM` 常量
- 保留 `EMBEDDING_DIM` 作为 fallback 零向量的维度常量（从参数获取）

**Patterns to follow:**
- 参考 `vector_store_dir` 参数的注入方式（第69行）

**Test scenarios:**
- 不传参数时使用默认配置值
- 传入自定义模型名称时使用该模型
- 模型加载失败时 graceful fallback

**Verification:**
- `SkillIndex()` 使用默认 embedding 模型
- `SkillIndex(embedding_model_name="all-mpnet-base-v2")` 使用指定模型

---

- [ ] **Unit 4: 修改 episodic_memory.py 使用配置化的 embedding**

**Goal:** 移除硬编码的模型名称

**Requirements:** R1, R5

**Dependencies:** Unit 1

**Files:**
- Modify: `src/ascend_op_agent/memory/episodic_memory.py`

**Approach:**
- `__init__` 新增 `embedding_model_name` 参数
- 存储模型名称供 `_store_episode` 和 `search_similar_episodes` 使用
- 移除第198行和第252行的硬编码模型名称

**Patterns to follow:**
- 参照 `vector_store` 参数的注入和存储方式

**Test scenarios:**
- 使用默认模型名称
- 使用自定义模型名称

**Verification:**
- `EpisodicMemory()` 使用默认 embedding 模型
- 自定义模型名称可被正确传递和使用

---

- [ ] **Unit 5: 修改 vector_store.py 移除硬编码常量并支持配置注入**

**Goal:** 统一 embedding 配置来源，移除重复定义

**Requirements:** R2, R3

**Dependencies:** Unit 1

**Files:**
- Modify: `src/ascend_op_agent/memory/vector_store.py`

**Approach:**
- `__init__` 参数 `persist_dir` 保持不变（已有配置注入模式）
- 移除模块级 `EMBEDDING_MODEL` 和 `EMBEDDING_DIM` 常量
- 添加 `embedding_dimension` 参数，存储备用维度常量
- ChromaDB 持久化目录默认路径改为从配置读取

**Patterns to follow:**
- 参照现有 `__init__` 参数模式

**Test scenarios:**
- 不传 `persist_dir` 时使用默认路径（从配置读取）
- 传入自定义路径时使用该路径
- 向量维度可正确配置

**Verification:**
- `VectorStore()` 使用配置中的默认 `persist_dir`
- `VectorStore(persist_dir="/custom/path")` 使用指定路径

---

- [ ] **Unit 6: 添加配置集成测试**

**Goal:** 验证配置系统与 embedding 模块的正确集成

**Requirements:** R1, R2, R3, R4, R5

**Dependencies:** Units 1-5

**Files:**
- Create: `tests/unit/test_embedding_config.py`

**Approach:**
- 测试配置加载和默认值
- 测试环境变量引用解析
- 测试各模块的配置注入

**Patterns to follow:**
- 参照 `tests/unit/` 目录现有测试风格

**Test scenarios:**
- `test_embedding_config_defaults`: 验证默认配置值
- `test_embedding_config_from_yaml`: 验证从 YAML 加载配置
- `test_embedding_model_env_var`: 验证环境变量引用
- `test_skill_index_with_custom_embedding`: 验证 SkillIndex 使用自定义配置
- `test_episodic_memory_with_custom_embedding`: 验证 EpisodicMemory 使用自定义配置
- `test_vector_store_with_custom_path`: 验证 VectorStore 使用自定义路径

**Verification:**
- 所有测试通过

## System-Wide Impact

- **Error propagation**: 配置错误时（无效模型）记录 warning，不阻断主流程
- **State lifecycle risks**: 无持久状态变更
- **API surface parity**: `VectorStore.__init__` 参数签名改变（`embedding_dimension` 新增）

## Risks & Dependencies

- **Risk**: 现有代码直接实例化 `VectorStore()` 而不传配置
  - **Mitigation**: 保持默认参数兼容，新增参数有默认值
- **Risk**: 配置的模型不存在
  - **Mitigation**: `sentence_transformers` 库会抛出异常，被捕获后记录 warning 并降级

## Documentation / Operational Notes

- 需要更新 `docs/configuration.md`（如果存在）说明新增配置项
- 无需修改部署脚本或启动命令

## Sources & References

- 相关代码: `src/ascend_op_agent/config.py`, `src/ascend_op_agent/skills/index.py`, `src/ascend_op_agent/memory/episodic_memory.py`, `src/ascend_op_agent/memory/vector_store.py`
- 配置文件: `config.yaml.example`
