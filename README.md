# Ascend Op Agent

昇腾算子开发Agent - 自动化六阶段算子开发工作流，支持本地/远程开发模式，集成MCP服务器和Skill知识库系统。

## 核心功能

- **六阶段工作流**: Phase0-5 自动完成算子开发
- **本地/远程模式**: 支持本地开发和SSH远程开发
- **MCP集成**: 连接外部MCP服务器扩展工具能力
- **Skill知识库**: 积累和复用算子开发经验
- **四层记忆系统**: Working/Episodic/Semantic/Procedural Memory
- **ACP编辑器适配器**: 支持 VS Code、Zed、JetBrains

## 环境要求

- Python >= 3.10
- Linux/macOS
- Node.js >= 16 (仅 TUI 模式)

## 安装

```bash
pip install -e .
```

## 快速开始

```bash
# 1. 配置
mkdir -p ~/.ascend_op_agent
cp config.yaml.example ~/.ascend_op_agent/config.yaml
cp .env.example ~/.ascend_op_agent/.env
vim ~/.ascend_op_agent/.env

# 2. 运行
ascend-op-agent run --mode local
```

详细说明请参考 [文档指南](#文档)。

## 配置

Ascend Op Agent 使用双文件配置架构：

| 文件 | 用途 | 版本控制 |
|------|------|----------|
| `config.yaml` | 行为配置（LLM模型、MCP服务器等） | ✅ 可提交 |
| `~/.ascend_op_agent/.env` | 敏感凭据（API密钥、Token等） | ❌ 不提交 |

### 环境变量语法

```yaml
# ${VAR} - 环境变量不存在时替换为空
api_key: "${OPENAI_API_KEY}"

# ${VAR:-default} - 环境变量不存在时使用默认值
model: "${EMBEDDING_MODEL:-sentence-transformers/all-MiniLM-L6-v3}"
```

详细配置请参考 [配置文档](docs/config.md)。

## TUI 交互模式

双进程 TUI 交互界面（需要 Node.js >= 16）：

```bash
ascend-op-agent run
```

详细使用指南请参考 [TUI 使用指南](docs/tui-guide.md)。

## 文档

| 文档 | 说明 |
|------|------|
| [用户文档](docs/) | 配置、工作流、Skill格式、TUI指南 |
| [开发指南](docs/development.md) | 项目结构、开发环境、调试 |
| [模块文档](docs/modules/) | 核心模块详细文档 |
| [架构设计](docs/architecture.md) | 系统架构、记忆系统、7层Prompt |
| [安全策略](SECURITY.md) | 凭据管理、安全最佳实践 |
| [贡献指南](CONTRIBUTING.md) | 代码规范、测试、Git工作流 |
| [变更日志](CHANGELOG.md) | 版本历史 |

### 模块文档索引

| 模块 | 文档 |
|------|------|
| MCP 服务器 | [docs/modules/mcp.md](docs/modules/mcp.md) |
| Skill 知识库 | [docs/modules/skills.md](docs/modules/skills.md) |
| SSH 远程开发 | [docs/modules/ssh.md](docs/modules/ssh.md) |
| 记忆系统 | [docs/modules/memory.md](docs/modules/memory.md) |
| ACP 适配器 | [docs/modules/acp.md](docs/modules/acp.md) |
| 工作流引擎 | [docs/modules/workflow.md](docs/modules/workflow.md) |

## 目录结构

```
ascend_op_agent/
├── agent/           # Agent核心引擎
│   ├── core.py      # AIAgent 主类
│   ├── memory.py    # 四层记忆系统
│   ├── context.py   # 上下文引擎
│   └── SOUL.md      # Agent 身份定义
├── workflow/       # 工作流引擎
│   ├── engine.py    # 工作流引擎
│   └── phases.py    # Phase0-8 阶段定义
├── mcp/            # MCP服务器集成
│   ├── client.py    # MCP 客户端
│   └── lifecycle.py # 生命周期管理
├── skills/         # Skill知识库
│   ├── repository.py # 技能仓库
│   └── index.py     # 技能索引
├── ssh/            # SSH远程开发
│   ├── manager.py   # SSH 管理器
│   └── sync.py     # 文件同步
├── memory/        # 记忆系统
│   ├── episodic_memory.py  # 情景记忆
│   └── semantic_memory.py  # 语义记忆
├── acp/           # ACP编辑器适配器
│   ├── adapter.py  # 适配器主类
│   └── protocol.py # 协议定义
├── backend/       # RPC后端服务
├── security/      # 安全模块
└── cli.py         # CLI入口
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
PYTHONPATH=src python -m pytest tests/ -v

# 代码格式化
black src/ tests/
ruff check --fix src/ tests/
```

详细开发指南请参考 [开发指南](docs/development.md)。

## License

Apache License 2.0

Copyright 2026 SimmerChan
