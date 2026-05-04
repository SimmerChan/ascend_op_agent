# 重构配置体系：参照 Hermes Agent 实现双文件架构

## Overview

将 Ascend Op Agent 的单文件配置架构重构为双文件架构（`config.yaml` + `.env`），参照 Hermes Agent 的设计，实现敏感凭据与行为配置的完全分离。

## Problem Frame

当前 Ascend Op Agent 使用单文件 `config.yaml` + `${ENV_VAR}` 内联引用存在以下问题：

1. **敏感信息隔离不足**: API keys、tokens、passwords 等敏感信息虽然通过 `${ENV_VAR}` 引用，但仍与行为配置混在一起
2. **配置示例不完整**: `config.yaml.example` 仅包含行为配置示例，缺少敏感凭据的模板
3. **环境变量无默认值**: `${ENV_VAR}` 语法不支持 `${ENV_VAR:-default}` 默认值格式
4. **无优先级机制**: `.env` 文件不能覆盖 YAML 配置，用户无法便捷地临时覆盖配置
5. **缺乏配置验证**: 配置加载时无 schema 验证

**设计约束**:
- **C1**: 保持向后兼容，现有 `${ENV_VAR}` 用法不变
- **C2**: `.env` 文件加载对现有调用透明，自动进行

## Requirements Trace

- R1. 实现双文件架构：`config.yaml`（行为配置）+ `.env`（敏感凭据）
- R2. 创建 `.env.example` 模板，列出所有支持的敏感配置项
- R3. 支持 `${ENV_VAR:-default}` 格式的默认值
- R4. `.env` 中的值优先级高于 `config.yaml`
- R5. 保持向后兼容：现有 `${ENV_VAR}` 用法不变 ← C1
- R6. 添加配置验证机制
- R7. 配置加载时自动读取 `.env` 文件 ← C2

## Scope Boundaries

**包含**:
- `src/ascend_op_agent/config.py` 重构
- 新增 `.env.example` 模板文件
- 更新 `config.yaml.example` 行为配置示例
- 配置验证逻辑
- 扩展 `tests/test_config.py` 覆盖新功能
- 更新 README.md 说明新配置架构

**不包含**:
- 安全凭据的加密存储（future work）
- 配置热重载机制（future work）
- 配置 CLI 命令增强（future work）

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/config.py` - 当前配置实现，185 行
- `config.yaml.example` - 当前配置示例
- `tests/test_config.py` - 配置测试，204 行
- Hermes Agent: `hermes_cli/config.py` - 参考实现
- Hermes Agent: `.env.example` - 环境变量模板

### Hermes Agent 设计参考

```
~/.hermes/
├── cli-config.yaml    # 行为配置（可版本控制）
└── .env               # 敏感凭据（永不提交）
```

**关键设计原则**:
1. `.env` 文件路径: `~/.ascend_op_agent/.env`
2. `config.yaml` 中的 `${ENV_VAR}` 引用在 `.env` 存在时优先使用 `.env` 的值
3. 支持 `${ENV_VAR:-default}` 语法
4. 配置加载时同时解析 `.env` 文件

## Key Technical Decisions

- **D1 - 双文件架构**: `config.yaml` 存行为配置，`.env` 存敏感凭据。**理由**: 参照 Hermes Agent 成熟设计，实用且安全。
- **D2 - 延迟加载 `.env`**: `.env` 文件在 `load_config()` 时自动加载。**理由**: 避免非异步上下文问题，与现有代码兼容。
- **D3 - Pydantic Field 验证**: 使用 Pydantic 的 `Field` 装饰器添加配置验证。**理由**: 与现有代码风格一致。
- **D4 - 向后兼容**: 保持 `${ENV_VAR}` 语法不变，仅增强解析能力。**理由**: 减少升级成本。

## High-Level Technical Design

```mermaid
graph LR
    A[config.yaml] -->|YAML 行为配置| C["Modified:\nConfig.from_file"]
    B[.env] -->|敏感凭据| C
    C --> D{解析 env refs}
    D -->|${VAR:-default}| E[默认值]
    D -->|${VAR}| F[.env 值优先<br/>否则系统环境变量]
    E --> G[Resolved Config]
    F --> G
```

**配置加载优先级** (高到低):
1. `.env` 文件中的值（优先级最高）
2. `config.yaml` 中的 `${ENV_VAR}` 引用（查找 `.env` 或系统环境变量）
3. `${ENV_VAR:-default}` 的默认值（最低优先级）

## Implementation Units

- [ ] **Unit 1: 创建 `.env.example` 模板文件**

**Goal:** 创建完整的环境变量模板，列出所有敏感配置项

**Files:**
- Create: `.env.example`

**Approach:**
- 参照 Hermes Agent 的 `.env.example`
- 列出所有敏感配置项：API keys、tokens、passwords
- 添加详细注释说明每个变量的用途

**Test scenarios:**
- `.env.example` 包含所有必需的环境变量占位符
- 每个变量有清晰的中文注释说明用途

---

- [ ] **Unit 2: 更新 `config.yaml.example` 行为配置示例**

**Goal:** 简化 `config.yaml.example`，移除敏感信息占位符，专注于行为配置

**Files:**
- Modify: `config.yaml.example`

**Approach:**
- 移除 API keys、tokens 等敏感信息的占位符
- 保留行为配置示例
- 添加注释说明敏感配置应放在 `.env` 中

---

- [ ] **Unit 3: 重构 `src/ascend_op_agent/config.py`**

**Goal:** 实现双文件架构，支持 `.env` 加载和 `${ENV_VAR:-default}` 语法

**Dependencies:** None（可独立于 Unit 1、Unit 2 并行开发）

**Files:**
- Modify: `src/ascend_op_agent/config.py`

**Approach:**
1. 使用 `python-dotenv` 库的 `load_dotenv()` 函数加载 `.env` 文件到环境变量
2. 扩展 `_resolve_env_vars()` 支持 `${VAR:-default}` 语法
   - 正则表达式从 `\$\{([^}]+)\}` 改为 `\$\{([^}:-]+)(?::-([^}]*))?\}` 以支持 `:-` 默认值语法
3. 实现 `.env` 值覆盖 `config.yaml` 的优先级机制
4. 添加配置验证装饰器
5. 在 `config.py` 顶部添加: `from dotenv import load_dotenv`

**Patterns to follow:**
- Hermes Agent `_load_env()` 模式
- 现有 `config.py` 的 `Config` 类结构
- Pydantic `Field` 验证模式

**Test scenarios:**
- `load_config()` 自动加载 `.env`
- `${VAR:-default}` 解析正确
- `.env` 值覆盖 YAML 配置
- 缺失 `.env` 不影响加载

**Verification:**
- Unit 3 的所有 Test scenarios 验证通过即视为完成

---

- [ ] **Unit 4: 更新 `load_config()` 函数**

**Goal:** 在 `load_config()` 中集成 `.env` 加载逻辑

**Dependencies:** Unit 3

**新增依赖:**
- `python-dotenv` 库（需添加到 `pyproject.toml` 或 `requirements.txt`）

**Files:**
- Modify: `src/ascend_op_agent/config.py`

**Approach:**
- `load_config()` 调用时自动加载 `.env`
- `.env` 路径: `~/.ascend_op_agent/.env`
- 使用 `python-dotenv` 库解析 `.env` 文件

**Verification:**
- `load_config()` 在有 `.env` 时正确解析并优先使用其中的值

---

- [ ] **Unit 5: 添加配置验证逻辑**

**Goal:** 添加 Pydantic Field 验证，确保必需配置项存在

**Dependencies:** Unit 3

**Files:**
- Modify: `src/ascend_op_agent/config.py`
- Create: `tests/test_config_validation.py`（与现有 `tests/test_config.py` 保持同一目录结构）

**Approach:**
- 为 `api_key`、`password` 等敏感字段添加 `Field` 验证
- 添加 `model_validator` 检查配置合法性
- 提供有意义的错误信息

**Test scenarios:**
- `api_key` 为空时抛出 `ValidationError`
- `provider` 为非法值时抛出验证错误
- 嵌套配置验证正常工作

---

- [ ] **Unit 6: 更新测试覆盖**

**Goal:** 扩展 `tests/test_config.py` 覆盖新功能

**Dependencies:** Unit 3, Unit 4

**Files:**
- Modify: `tests/test_config.py`

**Test scenarios:**
- `.env` 文件加载测试
- `${VAR:-default}` 默认值测试
- 优先级覆盖测试
- 配置验证测试

---

- [ ] **Unit 7: 更新文档**

**Goal:** 更新 README 和相关文档说明新配置架构

**Dependencies:** Unit 1, Unit 2

**Files:**
- Modify: `README.md`

**Approach:**
- 添加双文件架构说明
- 更新配置加载示例
- 添加 `.env` 设置指南

## System-Wide Impact

- **Config 模块**: `load_config()` 行为变更，需要测试验证向后兼容
- **CLI**: `--config` 参数可能需要调整路径处理
- **Backend**: 潜在影响 `load_config()` 的所有调用点

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| 现有代码依赖 `config.yaml` 直接路径 | Low | Medium | 保持向后兼容，通过 `load_config()` 统一加载 |
| `.env` 文件不存在时行为变更 | Medium | Low | 优雅降级，不存在时使用原有逻辑 |
| 用户现有配置包含敏感信息 | Medium | Medium | 提供迁移脚本和文档 |

## Open Questions

### Resolved During Planning

- **Q1: 是否需要加密存储敏感凭据?** → 延期，v2.0 可能考虑。当前使用 `.env` 隔离已是合理改进。
- **Q2: 如何处理现有的 `config.yaml` 中的敏感信息?** → 用户应迁移到 `.env`，文档提供迁移指南。

### Deferred to Implementation

- **Q3: 是否支持 `load_config()` 热重载?** → 延期，作为 future work。

## Documentation / Operational Notes

1. 用户需要创建 `~/.ascend_op_agent/.env` 文件
2. 创建 `.env` 文件后需设置受限权限: `chmod 600 ~/.ascend_op_agent/.env`，防止其他本地用户读取敏感凭据
3. 将 `~/.ascend_op_agent/.env` 添加到项目 `.gitignore` 文件，防止意外提交
4. `config.yaml` 中的敏感信息占位符应更新为 `${ENV_VAR}` 引用
5. 升级路径：现有用户可继续使用 `${ENV_VAR}` 语法，新架构自动加载 `.env`

## Sources & References

- Hermes Agent `.env.example` - 环境变量模板参考
- Hermes Agent `hermes_cli/config.py` - 配置加载实现参考
- Ascend Op Agent `config.py` - 现有实现
- Ascend Op Agent `config.yaml.example` - 当前配置示例
