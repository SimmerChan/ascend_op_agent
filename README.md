# Ascend Op Agent

昇腾算子开发Agent - 自动化六阶段算子开发工作流，支持本地/远程开发模式，集成MCP服务器和Skill知识库系统。

## 核心功能

- **六阶段工作流**: Phase0-5 自动完成算子开发
- **本地/远程模式**: 支持本地开发和SSH远程开发
- **MCP集成**: 连接外部MCP服务器扩展工具能力
- **Skill知识库**: 积累和复用算子开发经验

## 环境要求

- Python >= 3.10
- Linux/macOS

## 安装

```bash
pip install -e .
```

## 配置

Ascend Op Agent 使用双文件配置架构：

| 文件 | 用途 | 版本控制 |
|------|------|----------|
| `config.yaml` | 行为配置（LLM模型、MCP服务器等） | ✅ 可提交 |
| `~/.ascend_op_agent/.env` | 敏感凭据（API密钥、Token等） | ❌ 不提交 |

### 初始化配置

```bash
# 1. 复制配置文件
mkdir -p ~/.ascend_op_agent
cp config.yaml.example ~/.ascend_op_agent/config.yaml

# 2. 创建环境变量文件
cp .env.example ~/.ascend_op_agent/.env
chmod 600 ~/.ascend_op_agent/.env  # 设置受限权限

# 3. 编辑 .env 填入你的 API 密钥
vim ~/.ascend_op_agent/.env
```

### 环境变量语法

配置文件中支持两种环境变量引用语法：

```yaml
# ${VAR} - 环境变量不存在时替换为空
api_key: "${OPENAI_API_KEY}"

# ${VAR:-default} - 环境变量不存在时使用默认值
model: "${EMBEDDING_MODEL:-sentence-transformers/all-MiniLM-L6-v3}"
```

### 配置加载优先级

1. `.env` 文件中的值（最高优先级）
2. `config.yaml` 中的环境变量引用
3. `${VAR:-default}` 中的默认值（最低优先级）

## 快速开始

```bash
# 运行Agent（确保已配置 .env 文件）
ascend-op-agent run --mode local
```

## TUI 交互模式

Ascend Op Agent 支持双进程 TUI 交互界面（需要 Node.js >= 16）：

```bash
ascend-op-agent run
```

详细使用指南请参考 [TUI 使用指南](docs/tui-guide.md)。

## 目录结构

```
ascend_op_agent/
├── agent/          # Agent核心引擎
├── workflow/       # 工作流引擎
├── ssh/            # SSH远程开发
├── mcp/            # MCP服务器集成
├── skills/         # Skill知识库
└── security/       # 安全层
```

## 开发指南

```bash
# 运行测试
PYTHONPATH=src python -m pytest tests/ -v
```

## License

Apache License 2.0