# Ascend Op Agent

昇腾算子开发 Agent — 自研轻量状态机编排器 + cannbot-skills 知识层 + 910B 真编译 + ST 驱动真算子验证。

**P0 + P1 已完成**（2026-06-23 → 2026-07-05）：
- ✅ 自研 PhaseRunner 状态机（13 节点顺序+条件+HITL）
- ✅ 910B 真编译（build.sh + compile cosmetic fix）
- ✅ 真算子验证（ST 驱动 + 10/10 precision PASS）
- ✅ N=20 stress 100% PASS（Minimax MiniMax-M3）
- ✅ CLI 端到端 5/5 PASS（op: 前缀 → PhaseRunner → done）
- ✅ ship gate 4 步全过 → 🚢 SHIP READY

> v1.0.0 发布于 2026-07-06。之后的迭代进展（skill 结晶化、任务管理层、LLM adapter streaming 重构等）见 [CHANGELOG.md](CHANGELOG.md) 的 [Unreleased] 段。

## Quick Start

```bash
# 1. 安装
pip install -e .

# 2. 配置 LLM（~/.ascend_op_agent/config.yaml）
llm:
  provider: "anthropic"
  api_key: "<your-key>"
  api_base: "https://api.minimaxi.com/anthropic"
  model: "MiniMax-M3"
remote:
  host: "192.168.9.105"      # 910B 机器
  user: "root"
  container_name: "ops_pt"    # CANN 开发容器

# 3. Ship gate（唯一 boolean gate）
PYTHONPATH=src python scripts/ship_ready.py
# → lint ✅ unit_test ✅ stress ✅ e2e_tui ✅ → 🚢 SHIP READY

# 4. 单次 e2e（验证算子编译+精度）
PYTHONPATH=src python scripts/e2e_real_op.py
# → compile success + precision 10/10 PASS + done
```

## 核心功能

- **自研状态机编排器**: PhaseRunner（节点/边/条件/HITL）+ CheckpointStore（SQLite v2 schema + v1↔v2 迁移 + rollback）
- **cannbot-skills 知识层**: 消费华为官方 skill 仓库（cuda2ascend-simt / triton 5-skill 链），hybrid 映射
- **910B 真编译**: SSH → docker exec ops_pt → build.sh --soc=ascend910b（compile cosmetic fix 处理 build.sh false negative）
- **ST 驱动真算子验证**: NPU 跑 + CPU golden + MERE/MARE 精度比对（CANN 社区标准）
- **N=20 stress**: dual metric（first-try ≥80% + with-retry ≥95%）+ 3-state exit code
- **CLI 端到端**: op: 前缀路由 → PhaseRunner → agent.progress 通知 → SIGTERM graceful + heartbeat
- **LLM 多 Provider 轮询**: 三 provider 互备（Minimax MiniMax-M3 主力 / 智谱 GLM-5.2 备 1 / 火山 Ark GLM-5.2 备 2），全 Anthropic 兼容协议；adapter streaming 总开 + per-provider max_tokens 自适应上限（Minimax 256K / GLM 128K / Ark 64K）
- **ACP编辑器适配器**: 支持 VS Code、Zed、JetBrains
- **对话可视化器**: 树形结构调试 Agent 与用户的完整交互流程

## CLI 命令

| 命令 | 说明 |
|------|------|
| `ascend-op-agent init` | 初始化项目配置和工作目录 |
| `ascend-op-agent run` | 启动 Agent 对话（TUI 交互界面） |
| `ascend-op-agent skill list` | 列出已安装的 Skills |
| `ascend-op-agent skill add <url>` | 添加外部 Skill 仓库 |
| `ascend-op-agent skill install <url>` | 从仓库安装 Skill（交互式选择） |
| `ascend-op-agent skill remove <name>` | 移除外部 Skill 仓库 |
| `ascend-op-agent mcp list` | 列出已配置的 MCP 服务器 |
| `ascend-op-agent mcp start <name>` | 启动 MCP 服务器 |
| `ascend-op-agent mcp stop <name>` | 停止 MCP 服务器 |
| `ascend-op-agent mcp status` | 查看 MCP 服务器状态 |
| `ascend-op-agent acp` | ACP 编辑器集成模式（stdio 与 IDE 通信） |
| `ascend-op-agent sync push -f <files>` | 将本地文件同步到远程 |
| `ascend-op-agent sync pull -f <files>` | 将远程文件同步到本地 |
| `ascend-op-agent viewer` | 启动 Agent Conversation Visualizer 可视化调试工具 |
| `ascend-op-agent viewer --only-backend` | 仅启动后端 API 服务 |
| `ascend-op-agent viewer --port <port>` | 指定后端服务端口（默认 3001） |

### 命令示例

```bash
# 初始化项目
ascend-op-agent init --workspace ./workspace

# 启动 Agent 对话
ascend-op-agent run

# 管理 Skills
ascend-op-agent skill list
ascend-op-agent skill install https://gitcode.com/ascend/agent-skills

# 管理 MCP 服务器
ascend-op-agent mcp list
ascend-op-agent mcp start code-search

# 使用 ACP 编辑器模式
ascend-op-agent acp

# 文件同步
ascend-op-agent sync push -f src/ops
ascend-op-agent sync pull -f build/output.cce

# 启动可视化调试工具
ascend-op-agent viewer
ascend-op-agent viewer --port 8080
```

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
cp config.example.yaml ~/.ascend_op_agent/config.yaml
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
| LLM Provider 配置 | [docs/modules/llm_providers.md](docs/modules/llm_providers.md) |

## 目录结构

```
ascend_op_agent/
├── agent/              # Agent 核心引擎
│   ├── core.py          # AIAgent 主类（会话循环 + 工具调用）
│   ├── prompt_builder.py # 7 层 Prompt 组装
│   ├── memory.py        # Working Memory
│   ├── context.py       # 上下文引擎
│   ├── tool_registry.py # 工具注册
│   ├── session_manager.py / session_record.py  # 会话持久化
│   ├── skill_standards.py # Skill 规范
│   ├── SOUL.md          # Agent 身份定义
│   ├── providers/       # 多 Provider LLM 适配器（anthropic/openai/gemini/openrouter/azure/ollama，streaming + per-provider max_tokens）
│   └── tools/           # 工具集（file_read/write/search、patch、shell_exec、python_exec、git_*、npu_smi、msop、skill_manage）
├── orchestrator/       # 自研状态机编排器（非 LangGraph）
│   ├── state_machine.py # PhaseRunner（13 节点顺序+条件+HITL）
│   ├── checkpoint.py    # CheckpointStore（SQLite v2 + v1↔v2 迁移 + WAL + rollback）
│   ├── fix_loop.py      # review->fix->re-review 闭环
│   ├── npu_exec.py      # NpuExecutor（SSH -> docker exec ops_pt -> build.sh + ST 驱动）
│   ├── cannbot_loader.py # cannbot-skills 加载器 + SkillUsageRegistry（signal-1 跟踪）
│   ├── state.py         # OpState 类型定义 + reducer
│   ├── graphs/          # 三路径图定义（migration / new_dev）
│   └── nodes/           # 编排节点（common / hitl / delivery / migration / micro_mod / validation）
├── task_router/        # 任务类型路由（commands + executor_dispatch）
├── task_store/         # 任务存储与状态推导（models + store + progress + rollup）
├── backend/rpc/        # JSON-RPC 服务端（server.py + agent_service.py + notification_queue.py）
├── workflow/           # 工作流辅助（compiler / models / performance）
├── skills/             # Skill 知识库（repository / index / installer / models / storage / usage_tracker）
├── memory/             # 长期记忆（episodic / semantic / vector_store / llm_enhancer / system）
├── mcp/                # MCP 服务器集成（client / lifecycle / oauth / server_config）
├── ssh/                # SSH 远程开发（manager / sync / env_config / base_environment）
├── acp/                # ACP 编辑器适配器（adapter / protocol / session）
├── security/           # 凭据管理与 Token 解析
├── viewer/             # Agent 对话可视化器（FastAPI 后端 + Vue 3 + Element Plus 前端，接 CheckpointStore）
└── cli.py              # CLI 入口
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
