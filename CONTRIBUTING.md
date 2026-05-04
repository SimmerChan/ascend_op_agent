# 贡献指南

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

欢迎贡献 Ascend Op Agent！本指南将帮助您开始开发。

## 开发环境设置

### 前置要求

- Python >= 3.10
- Node.js >= 16 (用于 TUI 前端)
- Git

### 克隆仓库

```bash
git clone https://github.com/your-repo/ascend-op-agent.git
cd ascend-op-agent
```

### 安装依赖

```bash
# 安装项目依赖
pip install -e .

# 安装开发依赖
pip install -e ".[dev]"

# 安装前端依赖 (可选，仅 TUI 开发需要)
cd frontend && npm install && cd ..
```

### 配置开发环境

```bash
# 复制配置示例文件
cp config.yaml.example config.yaml
mkdir -p ~/.ascend_op_agent
cp .env.example ~/.ascend_op_agent/.env

# 编辑凭据配置
vim ~/.ascend_op_agent/.env
```

## 代码规范

### Python 代码风格

项目使用以下工具进行代码格式化：

- **Black**: 代码格式化 (line-length: 100)
- **Ruff**: Linting 和 import 排序

```bash
# 格式化代码
black src/ tests/

# 检查代码
ruff check src/ tests/

# 自动修复
ruff check --fix src/ tests/
```

### 导入顺序

按以下顺序组织导入：

1. Python 标准库模块
2. Python 第三方模块
3. 自定义模块

```python
# 标准库
import os
import re
from typing import Optional

# 第三方库
import click
from pydantic import BaseModel

# 本地模块
from ascend_op_agent.agent.core import AIAgent
```

### 文档字符串

使用 Google 风格的文档字符串：

```python
def resolve_token(token: Optional[str]) -> Optional[str]:
    """解析Token

    支持 ${ENV_VAR} 格式的环境变量引用。

    Args:
        token: Token值（支持 ${ENV_VAR} 格式）

    Returns:
        解析后的Token

    Example:
        >>> resolver = TokenResolver()
        >>> resolver.resolve_token("${API_KEY}")
        'actual-key-value'
    """
    pass
```

### Apache 2.0 许可声明

每个源文件必须包含以下许可声明：

```python
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
```

## 测试

### 运行测试

```bash
# 运行所有测试
PYTHONPATH=src python -m pytest tests/ -v

# 运行特定测试文件
PYTHONPATH=src python -m pytest tests/test_config.py -v

# 运行带有覆盖率报告的测试
PYTHONPATH=src python -m pytest tests/ --cov=ascend_op_agent --cov-report=html
```

### 测试目录结构

```
tests/
├── unit/                    # 单元测试
│   ├── agent/
│   ├── memory/
│   ├── skills/
│   └── acp/
├── integration/             # 集成测试
│   ├── test_acp.py
│   ├── test_e2e_local.py
│   └── test_e2e_remote.py
└── test_*.py               # 杂项测试
```

### 编写测试

```python
# tests/unit/test_example.py
import pytest
from ascend_op_agent.module import ClassName


class TestClassName:
    """测试类"""

    def test_method_success(self):
        """测试方法成功场景"""
        instance = ClassName()
        result = instance.method()
        assert result == expected

    @pytest.mark.asyncio
    async def test_async_method(self):
        """测试异步方法"""
        instance = ClassName()
        result = await instance.async_method()
        assert result == expected
```

### 异步测试

使用 `pytest-asyncio` 进行异步测试：

```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None
```

## Git 工作流

### 分支命名

- `main`: 主分支，稳定版本
- `develop`: 开发分支
- `feat/xxx`: 新功能
- `fix/xxx`: 修复
- `refactor/xxx`: 重构
- `docs/xxx`: 文档

### 提交规范

使用 Conventional Commits 格式：

```
<type>(<scope>): <subject>

<body>

<footer>
```

**类型 (type):**
- `feat`: 新功能
- `fix`: 修复
- `docs`: 文档
- `style`: 代码格式
- `refactor`: 重构
- `test`: 测试
- `chore`: 构建/工具

**示例:**

```bash
git commit -m "feat(agent): add new context engine"
git commit -m "fix(mcp): resolve server connection timeout"
git commit -m "docs(config): update configuration guide"
```

### Pull Request

1. 创建分支
2. 开发并测试
3. 提交更改
4. 推送并创建 PR
5. 等待代码审查

PR 描述应包含：
- 更改内容
- 更改原因
- 测试结果
- 相关 Issue

## 项目结构

```
ascend_op_agent/
├── agent/           # Agent 核心引擎
│   ├── core.py     # AIAgent 主类
│   ├── memory.py   # 记忆系统
│   └── ...
├── workflow/       # 工作流引擎
├── mcp/           # MCP 服务器集成
├── skills/        # Skill 知识库
├── ssh/           # SSH 远程开发
├── memory/        # 记忆系统
├── acp/           # ACP 编辑器适配器
├── backend/        # RPC 后端服务
├── security/      # 安全模块
└── cli.py         # CLI 入口
```

## 常见问题

### ImportError

确保设置 PYTHONPATH：

```bash
export PYTHONPATH=src
```

### 前端构建失败

```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

### 测试失败

```bash
# 检查依赖
pip install -e ".[dev]"

# 清理缓存
rm -rf .pytest_cache
PYTHONPATH=src python -m pytest tests/ -v --cache-clear
```

## 反馈

- **Bug 报告**: GitHub Issues
- **功能建议**: GitHub Discussions
- **代码审查**: Pull Requests

## 许可证

贡献的代码将使用 Apache License 2.0 许可证。
