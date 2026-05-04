# 开发指南

<!--
Copyright 2026 SimmerChan

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

## 项目结构

```
ascend_op_agent/
├── agent/                  # Agent 核心引擎
│   ├── __init__.py
│   ├── core.py            # AIAgent 主类
│   ├── memory.py          # 四层记忆系统
│   ├── context.py         # 上下文引擎
│   ├── prompt_builder.py  # 7层 Prompt 组装
│   ├── tool_registry.py   # 工具注册
│   └── SOUL.md            # Agent 身份定义
├── workflow/              # 工作流引擎
│   ├── __init__.py
│   ├── engine.py          # 工作流引擎主类
│   ├── phases.py          # Phase0-8 阶段定义
│   ├── adapters.py        # 外部适配器
│   ├── compiler.py        # 编译器封装
│   ├── performance.py     # 性能评估
│   ├── skill_save.py      # Skill 保存
│   └── models.py          # 数据模型
├── mcp/                   # MCP 服务器集成
│   ├── __init__.py
│   ├── client.py          # MCP 客户端
│   ├── lifecycle.py       # 生命周期管理
│   ├── oauth.py           # OAuth 认证
│   └── server_config.py   # 服务器配置
├── skills/                # Skill 知识库
│   ├── __init__.py
│   ├── repository.py      # 技能仓库
│   ├── index.py           # 技能索引
│   ├── installer.py       # 技能安装
│   ├── storage.py         # 存储管理
│   ├── models.py          # 数据模型
│   ├── interactive.py     # 交互接口
│   └── hybrid_search.py   # 混合检索
├── ssh/                   # SSH 远程开发
│   ├── __init__.py
│   ├── manager.py         # SSH 管理器
│   ├── sync.py            # 文件同步
│   ├── env_config.py      # 环境配置
│   └── base_environment.py # 基础环境
├── memory/                # 记忆系统
│   ├── __init__.py
│   ├── episodic_memory.py  # 情景记忆
│   ├── semantic_memory.py  # 语义记忆
│   ├── vector_store.py     # 向量存储
│   ├── system.py          # 系统记忆
│   └── llm_enhancer.py    # LLM 增强
├── acp/                   # ACP 编辑器适配器
│   ├── __init__.py
│   ├── adapter.py         # 适配器主类
│   ├── protocol.py        # 协议定义
│   └── session.py         # 会话管理
├── backend/               # RPC 后端服务
│   ├── __init__.py
│   ├── rpc/
│   │   ├── __init__.py
│   │   ├── agent_service.py # Agent 服务
│   │   ├── protocol.py    # 协议定义
│   │   └── server.py      # RPC 服务器
│   └── backend.py         # 后端入口
├── security/              # 安全模块
│   ├── __init__.py
│   ├── credential_manager.py # 凭据管理
│   └── token_resolver.py  # Token 解析
├── config.py              # 配置加载
├── cli.py                 # CLI 入口
├── version.py             # 版本信息
└── __init__.py
```

## 环境设置

### 开发环境要求

- Python >= 3.10
- Node.js >= 16 (用于 TUI 前端)
- Git

### 安装开发依赖

```bash
# 克隆仓库
git clone <repository-url>
cd ascend-op-agent

# 安装项目
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"

# TUI 前端依赖 (可选)
cd frontend && npm install && cd ..
```

### 配置

```bash
# 创建配置
cp config.yaml.example config.yaml

# 创建环境变量文件
mkdir -p ~/.ascend_op_agent
cp .env.example ~/.ascend_op_agent/.env
chmod 600 ~/.ascend_op_agent/.env

# 编辑凭据
vim ~/.ascend_op_agent/.env
```

## 代码规范

### 格式化

```bash
# Black 格式化
black src/ tests/

# Ruff 检查
ruff check src/ tests/

# 自动化修复
ruff check --fix src/ tests/
```

### 导入顺序

```python
# 1. 标准库
import os
import re
from typing import Optional

# 2. 第三方库
import click
from pydantic import BaseModel

# 3. 本地模块
from ascend_op_agent.agent.core import AIAgent
```

### 文档字符串

```python
def method(param: str) -> bool:
    """简短描述

    详细描述（如果需要）。

    Args:
        param: 参数描述

    Returns:
        返回值描述

    Raises:
        ValueError: 异常条件

    Example:
        >>> method("test")
        True
    """
    pass
```

### 许可声明

每个文件必须包含 Apache 2.0 许可头：

```python
# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ...
```

## 测试

### 运行测试

```bash
# 所有测试
PYTHONPATH=src python -m pytest tests/ -v

# 单元测试
PYTHONPATH=src python -m pytest tests/unit/ -v

# 集成测试
PYTHONPATH=src python -m pytest tests/integration/ -v

# 特定文件
PYTHONPATH=src python -m pytest tests/test_config.py -v

# 覆盖率
PYTHONPATH=src python -m pytest tests/ --cov=ascend_op_agent --cov-report=html
```

### 编写测试

```python
import pytest
from ascend_op_agent.module import ClassName


class TestClassName:
    def test_method(self):
        """测试方法"""
        instance = ClassName()
        result = instance.method()
        assert result == expected

    @pytest.mark.asyncio
    async def test_async(self):
        """测试异步方法"""
        result = await async_function()
        assert result is not None
```

## 调试

### 日志调试

```bash
# 启用详细日志
ascend-op-agent run --debug --log-level trace
```

### IDE 调试

在 VS Code 中创建 `.vscode/launch.json`:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Module",
            "type": "debugpy",
            "request": "launch",
            "module": "ascend_op_agent",
            "args": ["run", "--mode", "local"],
            "env": {
                "PYTHONPATH": "${workspaceFolder}/src"
            }
        }
    ]
}
```

### 常见问题

#### ImportError

确保设置 PYTHONPATH:
```bash
export PYTHONPATH=src
```

#### 前端构建失败

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

## 架构说明

### Agent 核心

```mermaid
graph TB
    subgraph Agent["Agent 核心"]
        A[AIAgent] --> B[PromptBuilder]
        A --> C[ToolRegistry]
        A --> D[ContextEngine]
        A --> E[MemorySystem]
    end

    B --> F[7层Prompt]
    C --> G[工具调用]
    D --> H[混合检索]
    E --> I[四层记忆]
```

### 工作流引擎

```mermaid
graph LR
    A[Phase0] --> B[Phase1]
    B --> C[Phase2]
    C --> D[Phase3]
    D --> E[Phase4]
    E --> F[Phase5]
    F --> G[Phase7]
    G --> H[Phase8]
```

### TUI 架构

```mermaid
graph TB
    subgraph Frontend["TUI Frontend"]
        A[用户界面] --> B[输入处理]
        B --> C[状态渲染]
    end

    subgraph Backend["Agent Backend"]
        D[JSON-RPC] --> E[Agent 引擎]
        E --> F[工具调用]
        F --> G[结果输出]
    end

    A --> |stdin/stdout| D
    C --> |状态更新| A
```

## 模块依赖

```
agent/
├── core.py          # 无循环依赖
├── memory.py        # 依赖 memory/
├── context.py       # 依赖 memory/
├── prompt_builder.py # 依赖 agent/
└── tool_registry.py # 无循环依赖

workflow/
├── engine.py        # 依赖 agent/, mcp/, ssh/, skills/
├── phases.py        # 依赖 workflow/models.py
└── adapters.py      # 依赖 workflow/models.py

mcp/
├── client.py        # 依赖 mcp/, security/
├── lifecycle.py     # 依赖 mcp/
└── oauth.py         # 依赖 security/

skills/
├── repository.py    # 依赖 skills/models.py
├── index.py         # 依赖 memory/vector_store.py
└── installer.py     # 依赖 skills/storage.py

ssh/
├── manager.py       # 依赖 ssh/, security/
└── sync.py         # 依赖 ssh/base_environment.py

memory/
├── episodic_memory.py # 依赖 memory/vector_store.py
├── semantic_memory.py # 依赖 memory/vector_store.py
└── vector_store.py    # 无循环依赖

acp/
├── adapter.py       # 依赖 acp/
├── protocol.py      # 无循环依赖
└── session.py       # 依赖 acp/protocol.py

backend/
└── rpc/
    ├── server.py    # 依赖 backend/rpc/
    └── agent_service.py # 依赖 agent/
```
