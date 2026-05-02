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

## 快速开始

```bash
# 初始化配置
cp config.yaml.example config.yaml

# 运行Agent
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