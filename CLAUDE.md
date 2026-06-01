# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Ascend Op Agent — 昇腾算子开发 Agent，支持六阶段自动化工作流（Phase0-5）、双进程 TUI 交互界面、SSH 远程开发和 Skill 知识库。

## 常用命令

```bash
# 安装（项目根目录）
pip install -e .

# 切换环境
conda activate py311

# Python 测试
PYTHONPATH=src python -m pytest tests/ -v
PYTHONPATH=src python -m pytest tests/test_agent.py -v  # 单文件

# 代码格式化和检查
black src/ tests/
ruff check --fix src/ tests/

# TUI 前端（Node.js）
cd frontend && npm install && npm run dev

# Viewer 可视化工具（双进程）
ascend-op-agent viewer          # 前端(port 3001) + 后端(port 3002)
ascend-op-agent viewer --only-backend  # 仅后端
```

### Agent 工具集

Agent 提供以下工具（定义在 `agent/tools/`）：

| 工具 | 说明 |
|------|------|
| `file_read` | 读取文件内容（带大小限制、loop 检测） |
| `file_write` | 写入文件（原子写入、备份） |
| `file_search` | 正则/glob 搜索文件内容或名称 |
| `patch` | 文本替换（精确/模糊匹配） |
| `shell_exec` | 执行 Shell 命令 |
| `python_exec` | 执行 Python 脚本（子进程隔离） |
| `git_log` | 查询 Git 提交历史 |
| `git_diff` | 查看 Git 差异 |
| `git_status` | 查看 Git 仓库状态 |
| `git_branch` | 列出 Git 分支 |
| `npu_smi` | 查询昇腾 NPU 设备信息 |
| `msop` | CANN 算子分析工具 |
| `cann_compile` | CANN 算子编译 |

## 系统架构

### 双进程 TUI 架构

```
┌─────────────────────────────────────────────────────┐
│  TUI Frontend (Node.js + Ink + React)             │
│  端口 3002 | 进程间通过 stdio JSON-RPC 通信         │
│  状态: idle/running/waiting_confirm/completed/error │
└───────────────────────┬─────────────────────────────┘
                        │ stdin/stdout (JSON-RPC 2.0)
                        ▼
┌─────────────────────────────────────────────────────┐
│  Agent Backend (Python)                            │
│  ThreadPoolExecutor 运行 AIAgent                   │
│  响应 agent.progress 通知更新进度条                  │
│  响应 agent.thinking 通知更新 thinking 状态          │
└─────────────────────────────────────────────────────┘
```

### Viewer 可视化工具（独立架构）

```
┌──────────────────┐     ┌──────────────────────────┐
│ Vue 3 Frontend   │────▶│ FastAPI Backend (3001)    │
│ (端口 3001)      │     │ /api/sessions/{id}/tree   │
│                  │     │ 会话记录树形结构 API       │
└──────────────────┘     └──────────────────────────┘
        │                         ▲
        │        Vite 代理         │
        ▼                         │
┌──────────────────┐              │
│ Vite Dev Server  │──────────────┘
│ (端口 3002)      │  /api/* 代理到 3001
└──────────────────┘
```

### 核心模块依赖

```
agent/core.py (AIAgent)
  ├── agent/prompt_builder.py  (7层Prompt组装)
  ├── agent/tool_registry.py    (工具注册)
  ├── agent/memory.py           (四层记忆)
  └── agent/context.py          (上下文引擎)

backend/rpc/agent_service.py (AgentAsyncWrapper)
  └── agent/core.py            (同步执行封装)

backend/rpc/server.py (JSONRPCServer)
  └── send_notification(method, params)  → stdout
```

### Agent 执行流程与通知机制

`AIAgent.run_conversation()` 是同步循环，每个迭代：
1. 组装 7 层 Prompt
2. 调用 LLM（`LLMClient.call()`）
3. 解析工具调用（如有）
4. 执行工具（`_execute_tool_call()`）
5. 追加到对话历史

**前端期望的进度通知**（定义在 `frontend/src/hooks/useRPC.ts`）：
- `backend.ready` — 后端就绪
- `agent.thinking` — LLM 思考中
- `agent.progress` — `{ phase: number, percent: number }`
- `agent.error` — 执行出错

当前 `AIAgent` **不发送这些通知**，只有最终响应返回后才切换状态。

### 配置：双文件架构

| 文件 | 内容 | 版本控制 |
|------|------|----------|
| `config.yaml` | LLM 模型、MCP 服务器等 | ✅ 提交 |
| `~/.ascend_op_agent/.env` | API 密钥等敏感信息 | ❌ 不提交 |

## 目录结构

```
src/ascend_op_agent/
├── agent/          # 核心引擎
│   ├── core.py      # AIAgent 主类
│   ├── SOUL.md      # Agent 身份定义
│   ├── memory.py    # Working Memory
│   └── providers/   # 多 Provider LLM 适配器
├── backend/rpc/    # JSON-RPC 服务端
│   ├── server.py    # JSONRPCServer（send_notification）
│   └── agent_service.py  # AgentAsyncWrapper
├── workflow/       # Phase0-5 工作流引擎
├── mcp/            # MCP 客户端
├── skills/         # Skill 知识库
├── memory/          # Episodic/Semantic Memory (ChromaDB)
├── ssh/            # SSH 远程开发
├── acp/            # ACP 编辑器适配器（stdio 协议）
└── viewer/         # Agent 对话可视化器（独立服务）
```

## 关键设计

### LLM Provider 适配器模式

`LLMClient` 使用注册表模式：`{"openai": OpenAIAdapter, "anthropic": AnthropicAdapter, ...}`。新增 Provider 只需添加适配器类，无需修改核心逻辑。

### 工具调用双模式

Agent 支持两种工具调用格式：
1. **Native Function Calling** — 结构化 `ToolCallResult`
2. **XML 格式兼容** — `<tool_call name="xxx">{...}</tool_call>`

### 会话记录

`SessionRecordManager` 持久化会话（LLMEntry、ToolEntry、UserEntry 等），支持树形结构查询。数据存储在 `~/.ascend_op_agent/sessions/`。

## 环境要求

- Python >= 3.10
- Node.js >= 16（TUI 前端）
- ChromaDB（向量存储，记忆系统）

## 调试

```bash
# 查看详细日志
ascend-op-agent run --debug

# TUI 前端卡住时检查后端进程
ps aux | grep ascend_op_agent
lsof -i :3002
```

## 方案设计参考

Ascend Op Agent任何特性方案在设计前，都必须先参考以下方案的设计。

1. hermes agent源码目录: /Users/huangshilei/Documents/pythonprojects/hermes-agent
   1. 架构说明wiki文档： /Users/huangshilei/Documents/pythonprojects/hermes-agent/.zread/
2. claude code源码目录: /Users/huangshilei/Documents/pythonprojects/claude-code
   1. 架构说明wiki文档： /Users/huangshilei/Documents/pythonprojects/claude-code/.zread/