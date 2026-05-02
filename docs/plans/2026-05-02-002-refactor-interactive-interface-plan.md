---
title: "refactor: Ascend Op Agent 交互界面双进程架构重构（Node.js + Ink）"
type: refactor
status: active
date: 2026-05-02
origin: "docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md"
---

# Ascend Op Agent 交互界面双进程架构重构规划

## Overview

将 Ascend Op Agent 的交互界面从当前的单进程 CLI 模式重构为 Hermes Agent 风格的双进程架构：**前端基于 Node.js + Ink（React for CLI）**，后端是承载完整 Agent 引擎的 Python 子进程，两者通过 **stdin/stdout 管道上的 newline-delimited JSON-RPC 2.0** 协议进行全双工通信。

## Problem Frame

**现状问题**：
- 当前 `cli.py` 的 `run` 命令使用简单的 `while True` + `console.input()` 循环
- Agent 推理（LLM 调用）是同步阻塞的，界面在推理期间完全冻结
- 没有前后端分离，长时推理会阻塞用户输入

**Hermes Agent 的解决方案**：
- 前端始终保持响应式渲染（帧率敏感）
- Agent 推理、工具调用、审批交互全部在后端线程池中完成
- 通过 JSON-RPC 2.0 实现全双工通信，解耦渲染和推理

**用户价值**：
- 推理期间界面保持响应（显示进度、允许取消）
- 支持复杂的审批交互（多选项、确认提示）
- 更好的用户体验（无冻结感）

## Requirements Trace

| ID | 需求 | 来源 |
|----|------|------|
| R1 | 会话初始化（本地/远程模式） | 原有 KD2 |
| R3 | 方案设计阶段（用户确认） | 原有 KD1 |
| R5 | 编译验证（自动修复最多3次） | 原有 KD3 |
| R9 | Skill仓库管理 | 原有 KD7 |
| R-NEW1 | 推理期间界面保持响应 | 本次重构新增 |
| R-NEW2 | 支持复杂审批交互（多选项输入） | 本次重构新增 |
| R-NEW3 | 支持进度显示和取消操作 | 本次重构新增 |

## Scope Boundaries

### In Scope
- 双进程架构设计与实现
- JSON-RPC 2.0 协议定义
- Node.js + Ink TUI 前端实现
- Python 后端 RPC 服务封装
- 前端组件库开发
- 协议层集成测试

### Out of Scope
- Python curses + rich 前端实现（放弃）
- 现有 Agent 引擎逻辑修改（仅重新封装）
- MCP 服务器和 Skill 仓库功能修改
- Node.js 前端的生产构建优化

## Key Technical Decisions

### KD-1: Node.js + Ink 作为前端框架
**决策**: 前端使用 Node.js + Ink（React for CLI）构建
**理由**: Hermes Agent 验证可行；React 声明式开发体验碾压 curses；丰富组件生态（ink-text-input 等）；HMR 热更新提升开发效率
**替代考虑**: Python curses + rich（API 晦涩、组件化困难）

### KD-2: TypeScript 作为前端语言
**决策**: 前端使用 TypeScript
**理由**: 类型安全、IDE 支持好、与现代 Node.js 生态一致
**替代考虑**: JavaScript（类型安全弱）

### KD-3: Python 后端作为子进程
**决策**: Node.js 前端 spawn Python 后端子进程，通过 stdin/stdout JSON-RPC 通信
**理由**: 与 Hermes Agent 架构一致；前端负责 UI 渲染和用户交互，后端负责 Agent 推理
**替代考虑**: Python 作为主进程（但 Hermes 验证了 Node.js 前端为主的架构）

### KD-4: 保留原有 Python CLI 入口
**决策**: `ascend-op-agent tui` 命令启动 Node.js 前端，前端再启动 Python 后端
**理由**: 用户使用习惯一致；渐进式迁移，原有 `run` 命令可保留
**替代考虑**: 完全替换为 Node.js 入口（需要用户安装 Node.js）

## High-Level Technical Design

```
TUI Frontend (Node.js + Ink)
─────────────────────────────────────────────────────────────
│  Main Process - TypeScript + React                       │
│  ├── App.tsx: Main component, state management          │
│  ├── components/: Ink components (Dialog, Progress...)  │
│  ├── hooks/useRPC: JSON-RPC client hook                 │
│  └── utils/ansi.ts: ANSI escape sequence handling       │
─────────────────────────────────────────────────────────────
                            │
                            │ spawn (stdin/stdout)
                            ▼
Agent Backend (Python) - Child Process
─────────────────────────────────────────────────────────────
│  ├── backend.py: Entry point, starts RPC service        │
│  ├── rpc/server.py: JSON-RPC 2.0 server                │
│  ├── agent/core.py: AIAgent engine (existing)          │
│  └── workflow/: Workflow engine (existing)              │
─────────────────────────────────────────────────────────────
```

### 项目结构

```
ascend_op_agent/
├── frontend/                          # Node.js + Ink 前端
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── index.tsx                 # Ink 入口
│   │   ├── App.tsx                   # 主应用组件
│   │   ├── components/
│   │   │   ├── Dialog.tsx            # 确认对话框
│   │   │   ├── ProgressBar.tsx       # 进度条
│   │   │   ├── MessageList.tsx       # 消息列表
│   │   │   └── StatusBar.tsx         # 状态栏
│   │   ├── hooks/
│   │   │   └── useRPC.ts             # RPC 客户端 hook
│   │   └── types/
│   │       └── index.ts              # 类型定义
│   └── dist/                          # 编译输出
│
├── src/ascend_op_agent/
│   ├── backend.py                     # Python 后端入口（新增）
│   ├── backend/
│   │   ├── __init__.py
│   │   └── rpc/                       # RPC 服务（新增）
│   │       ├── __init__.py
│   │       ├── server.py              # JSON-RPC 服务端
│   │       └── agent_service.py       # Agent 服务封装
│   └── ...                            # 现有代码
│
└── package.json                       # 根目录（Node.js 依赖）
```

### JSON-RPC 2.0 协议设计

**请求格式（Client → Server）**:
```json
{"jsonrpc": "2.0", "id": 1, "method": "agent.run", "params": {"user_input": "开发一个LayerNorm算子"}}
{"jsonrpc": "2.0", "id": 2, "method": "agent.cancel", "params": {}}
{"jsonrpc": "2.0", "id": 3, "method": "session.reset", "params": {}}
```

**响应格式（Server → Client）**:
```json
{"jsonrpc": "2.0", "id": 1, "result": {"status": "waiting_confirmation", "data": {...}}}
{"jsonrpc": "2.0", "id": 1, "result": {"status": "completed", "response": "算子开发完成"}}
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Invalid Request"}}
```

**通知格式（Server → Client，单向）**:
```json
{"jsonrpc": "2.0", "method": "agent.thinking", "params": {"message": "正在分析需求..."}}
{"jsonrpc": "2.0", "method": "agent.progress", "params": {"phase": 2, "percent": 45}}
{"jsonrpc": "2.0", "method": "agent.tool_call", "params": {"tool": "compile", "status": "running"}}
```

### 状态机设计

```
                    ┌──────────────┐
                    │    IDLE      │◄─────────────────────────┐
                    └──────┬───────┘                          │
                           │ user_input received              │
                           ▼                                   │
                    ┌──────────────┐                          │
            ┌───────│   RUNNING    │                          │
            │       └──────┬───────┘                          │
            │              │ tool_call / waiting              │
            │              ▼                                   │
            │       ┌──────────────┐                          │
            │       │   WAITING    │ (for confirmation/       │
            │       │  CONFIRM     │  user input)             │
            │       └──────┬───────┘                          │
            │              │ user_response                    │
            │              ▼                                   │
            │       ┌──────────────┐                          │
            │       │   RUNNING    │──────────────────────────┘
            │       └──────────────┘  (continue)
            │
            │ cancel_requested
            ▼
       ┌──────────────┐
       │  CANCELLED   │──────► IDLE (after reset)
       └──────────────┘
            │
            │ completed
            ▼
       ┌──────────────┐
       │  COMPLETED   │──────► IDLE (after user confirms new conversation)
       └──────────────┘
```

### 审批交互流程

**Phase 2 方案确认示例**:
```
Frontend ──JSON-RPC──► Backend: agent.run("开发一个LayerNorm算子")
Frontend ◄──notify─── Backend: agent.thinking("正在分析需求...")
Frontend ◄──notify─── Backend: agent.progress({phase: 2, percent: 50})
Frontend ◄──result─── Backend: {status: "waiting_confirmation", data: {
  "type": "confirm",
  "title": "方案设计确认",
  "content": "...",
  "options": ["yes", "no", "modify"]
}}
Frontend: 显示确认对话框，等待用户选择
User: 选择 "yes"
Frontend ──JSON-RPC──► Backend: agent.respond({choice: "yes"})
Frontend ◄──result─── Backend: {status: "running"}
... 继续执行 ...
Frontend ◄──result─── Backend: {status: "completed", response: "..."}
```

## Implementation Units

- [ ] **Unit 1: Python 后端 RPC 服务层**

**Goal:** 实现 Python 端的 JSON-RPC 2.0 服务端封装

**Requirements:** R-NEW1

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/backend/__init__.py`
- Create: `src/ascend_op_agent/backend/rpc/__init__.py`
- Create: `src/ascend_op_agent/backend/rpc/server.py` — JSON-RPC 2.0 服务端
- Create: `src/ascend_op_agent/backend/rpc/protocol.py` — 协议核心
- Create: `src/ascend_op_agent/backend/rpc/agent_service.py` — Agent 服务封装
- Create: `src/ascend_op_agent/backend.py` — 后端入口
- Create: `tests/unit/backend/rpc/test_server.py`
- Create: `tests/unit/backend/rpc/test_agent_service.py`

**Approach:**
- 手动实现简单版 JSON-RPC 2.0（符合规范，轻量无依赖）
- 使用 `asyncio` 处理异步任务
- stdin/stdout 作为传输层（与 Hermes Agent 一致）
- **stderr 必须消费**：启动后台线程读取 stderr，避免 pipe 阻塞
- Agent 推理在后台线程执行，不阻塞 RPC 服务

**Agent 异步封装**:
```python
# backend/rpc/agent_service.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict, Optional

class AgentResponse(TypedDict):
    """Agent 响应的类型定义"""
    status: str
    response: Optional[str]
    data: Optional[dict]


class AgentAsyncWrapper:
    """将同步 AIAgent 封装为异步接口"""

    def __init__(self, agent: AIAgent):
        self.agent = agent
        self._thread_pool = ThreadPoolExecutor(max_workers=4)

    async def run_conversation_async(self, user_input: str) -> AgentResponse:
        loop = asyncio.get_event_loop()
        # 在线程池中执行同步 LLM 调用
        result = await loop.run_in_executor(
            self._thread_pool,
            self.agent.run_conversation,
            user_input
        )
        return AgentResponse(status="completed", response=result, data=None)

    def run_conversation(self, user_input: str) -> str:
        """同步版本，供非异步上下文调用"""
        return self.agent.run_conversation(user_input)
```

**stderr 消费者**:
```python
# backend.py
import threading

def _consume_stderr(stderr, log_path):
    """后台线程消费 stderr，避免 pipe 阻塞"""
    with open(log_path, 'w') as f:
        for line in stderr:
            f.write(line)

# 启动后端时
process = subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE)
stderr_thread = threading.Thread(target=_consume_stderr, args=(process.stderr, 'backend.log'), daemon=True)
stderr_thread.start()
```

**Config 传递**: 后端从环境变量 `ASCEND_OP_AGENT_CONFIG` 读取配置文件路径，在 `backend.py` 入口初始化时加载。

**Test scenarios:**
- 解析有效 JSON-RPC 请求
- 生成有效 JSON-RPC 响应
- 异步任务执行和进度通知
- 取消操作处理
- stderr 后台线程消费（不阻塞）
- 后端就绪通知 (`backend.ready`) 发送

**Verification:**
- `python -m pytest tests/unit/backend/rpc/ -v` 通过

---

- [ ] **Unit 2: Node.js + Ink 前端项目脚手架**

**Goal:** 建立 Node.js + TypeScript + Ink 前端项目结构

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** None

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/src/index.tsx` — Ink 入口
- Create: `frontend/src/types/index.ts` — 类型定义
- Create: `frontend/.gitignore`
- Create: `tests/unit/frontend/` — 前端单元测试目录

**Approach:**
```json
// package.json
{
  "name": "@ascend-op-agent/tui",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "build": "tsc",
    "start": "node dist/index.js",
    "dev": "tsc && node dist/index.js"
  },
  "dependencies": {
    "ink": "^4.4.1",
    "react": "^18.2.0",
    "ink-text-input": "^5.0.1",
    "meow": "^11.0.0"
  },
  "devDependencies": {
    "@types/react": "^18.2.0",
    "@types/node": "^20.0.0",
    "typescript": "^5.3.0"
  }
}
```

**Patterns to follow:**
- Hermes Agent 的 Node.js 前端项目结构
- Ink 官方示例

**Test scenarios:**
- `npm install` 成功
- `npm run build` 编译成功
- `npm run start` 启动成功（需要 Python 后端）

**Verification:**
- `cd frontend && npm install && npm run build` 通过

---

- [ ] **Unit 3: Ink 组件库**

**Goal:** 实现 Ink React 组件库（对话框、进度条、消息列表等）

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 2

**Files:**
- Create: `frontend/src/components/Dialog.tsx` — 确认对话框
- Create: `frontend/src/components/ProgressBar.tsx` — 进度条
- Create: `frontend/src/components/MessageList.tsx` — 消息列表
- Create: `frontend/src/components/StatusBar.tsx` — 状态栏
- Create: `frontend/src/components/Spacer.tsx` — 空白组件
- Create: `tests/unit/frontend/components.test.tsx`

**Components 设计**:
```typescript
// Dialog.tsx - 确认对话框（含 ESC 取消支持）
import { Box, Text } from 'ink';
import { useInput } from 'ink';

interface DialogProps {
  title: string;
  message: string;
  options: string[];
  onSelect: (option: string) => void;
  onCancel?: () => void;  // ESC 取消回调
}

export const Dialog: React.FC<DialogProps> = ({ title, message, options, onSelect, onCancel }) => {
  const [selectedIndex, setSelectedIndex] = useState(0);

  // 使用 ink 的 useInput 处理键盘事件
  useInput((input, key) => {
    if (key.upArrow) {
      setSelectedIndex(i => Math.max(0, i - 1));
    } else if (key.downArrow) {
      setSelectedIndex(i => Math.min(options.length - 1, i + 1));
    } else if (key.return) {
      onSelect(options[selectedIndex]);
    } else if (key.escape && onCancel) {
      onCancel();
    }
  });

  return (
    <Box flexDirection="column" borderStyle="round" padding={1}>
      <Text bold>{title}</Text>
      {message && <Text>{message}</Text>}
      {options.map((option, i) => (
        <Text key={option} color={i === selectedIndex ? 'cyan' : undefined}>
          {i === selectedIndex ? '> ' : '  '}{option}
        </Text>
      ))}
      <Text dimColor>↑↓ 选择，Enter 确认，ESC 取消</Text>
    </Box>
  );
};

// ProgressBar.tsx - 进度条
import { Box, Text } from 'ink';

interface ProgressBarProps {
  label: string;
  percent: number; // 0-100
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ label, percent }) => {
  const filled = Math.floor(percent / 2.5);
  const empty = 40 - filled;
  const bar = '█'.repeat(filled) + '░'.repeat(empty);
  return (
    <Box flexDirection="column">
      <Text>{label}</Text>
      <Text>[{bar}] {percent}%</Text>
    </Box>
  );
};
```

**Patterns to follow:**
- Ink 官方组件 API
- Hermes Agent 的 TUI 组件设计

**Test scenarios:**
- 渲染确认对话框（含标题和选项列表）
- 渲染进度条
- 键盘导航（上下左右）
- Enter 确认选择
- ESC 取消操作（调用 onCancel 回调）
- onCancel 为空时 ESC 无响应（不报错）

**Verification:**
- `npm run build` 编译通过
- 组件在终端正常显示
- ESC 键按下时触发 onCancel

---

- [ ] **Unit 4: RPC 客户端 Hook**

**Goal:** 实现 TypeScript 的 JSON-RPC 客户端封装

**Requirements:** R-NEW1, R-NEW2

**Dependencies:** Unit 2

**Files:**
- Create: `frontend/src/hooks/useRPC.ts` — RPC 客户端 hook
- Create: `frontend/src/hooks/useBackendProcess.ts` — 后端进程管理
- Create: `tests/unit/frontend/hooks.test.ts`

**Approach:**
```typescript
// useRPC.ts
import { useState, useEffect, useCallback, useRef } from 'react';
import { spawn, ChildProcess } from 'child_process';

interface RPCMessage {
  jsonrpc: "2.0";
  id?: number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string };
}

interface UseRPCReturn {
  send: (method: string, params?: Record<string, unknown>) => number;
  messages: RPCMessage[];
  lastResponse: RPCMessage | null;
  isConnected: boolean;
  reset: () => void;  // 重置状态，开始新对话
}

export const useRPC = (backendModule: string = 'ascend_op_agent.backend'): UseRPCReturn => {
  const [messages, setMessages] = useState<RPCMessage[]>([]);
  const [lastResponse, setLastResponse] = useState<RPCMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const backendRef = useRef<ChildProcess | null>(null);

  // 启动后端进程 - 在 useEffect 内用 useRef 避免重复创建
  useEffect(() => {
    // Python 路径：优先使用环境变量，win32 使用 python.exe，其他平台使用 python3
    const pythonPath = process.platform === 'win32'
      ? (process.env.PYTHON_PATH || 'python.exe')
      : (process.env.PYTHON_PATH || 'python3');

    backendRef.current = spawn(pythonPath, ['-m', backendModule], {
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',  // 禁用 Python 输出缓冲
        TERM: process.env.TERM || 'xterm-256color',  // 支持 ANSI 颜色
      },
      stdio: ['pipe', 'pipe', 'pipe', 'pipe'],  // stdin, stdout, stderr, extra for TTY binding
    });

    // 确保 stdin 处于阻塞模式（Ink 需要 TTY）
    if (backend.stdin && !backend.stdin.destroyed) {
      backend.stdin.cork();
    }

    const backend = backendRef.current;

    backend.stdout.on('data', (data: Buffer) => {
      const lines = data.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        try {
          const msg: RPCMessage = JSON.parse(line);
          setMessages(prev => [...prev, msg]);
          setLastResponse(msg);
          if (msg.method === 'backend.ready') {
            setIsConnected(true);
          }
        } catch {}
      }
    });

    backend.stderr.on('data', (data: Buffer) => {
      // stderr 可用于调试日志，暂时忽略
      console.error('[backend stderr]', data.toString());
    });

    backend.on('exit', (code) => {
      setIsConnected(false);
      console.log(`[backend exited with code ${code}]`);
    });

    // 清理：组件卸载时终止后端
    return () => {
      if (backend && !backend.killed) {
        backend.kill();
      }
    };
  }, [backendModule]);

  const send = useCallback((method: string, params?: Record<string, unknown>): number => {
    const backend = backendRef.current;
    if (!backend || backend.stdin.destroyed) {
      throw new Error('Backend not connected');
    }
    const id = Date.now();
    const msg: RPCMessage = { jsonrpc: "2.0", id, method, params };
    backend.stdin.write(JSON.stringify(msg) + '\n');
    return id;
  }, []);

  const reset = useCallback(() => {
    // 发送 reset 请求，让后端重置状态
    send('session.reset', {});
    setMessages([]);
    setLastResponse(null);
  }, [send]);

  return { send, messages, lastResponse, isConnected, reset };
};
```

**Patterns to follow:**
- React hooks 模式
- Hermes Agent 的 RPC 客户端实现

**Test scenarios:**
- 成功连接后端
- 发送请求并接收响应
- 处理后端通知
- 连接断开处理

**Verification:**
- TypeScript 编译通过
- hook 逻辑正确

---

- [ ] **Unit 5: 主应用组件 App.tsx**

**Goal:** 实现主应用组件，整合所有 UI 组件和 RPC 逻辑

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 3, Unit 4

**Files:**
- Create: `frontend/src/App.tsx` — 主应用组件
- Create: `tests/unit/frontend/App.test.tsx`

**Approach:**
```typescript
// App.tsx
import { useState, useEffect } from 'react';
import { Box, Text } from 'ink';
import { useRPC } from './hooks/useRPC';
import { Dialog } from './components/Dialog';
import { ProgressBar } from './components/ProgressBar';
import { MessageList } from './components/MessageList';
import { StatusBar } from './components/StatusBar';
import { TextInput } from 'ink-text-input';

type AppState = 'idle' | 'running' | 'waiting_confirm' | 'completed' | 'error';

export const App: React.FC = () => {
  const [state, setState] = useState<AppState>('idle');
  const [input, setInput] = useState('');
  const [progress, setProgress] = useState({ phase: 0, percent: 0 });
  const [messages, setMessages] = useState<string[]>([]);
  const [confirmData, setConfirmData] = useState<{title: string; options: string[]} | null>(null);

  const { send, lastResponse, isConnected, reset } = useRPC();

  // 处理后端响应
  useEffect(() => {
    if (!lastResponse) return;

    if (lastResponse.method === 'agent.thinking') {
      setMessages(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${lastResponse.params?.message}`]);
    } else if (lastResponse.method === 'agent.progress') {
      setProgress(lastResponse.params || { phase: 0, percent: 0 });
    } else if (lastResponse.result?.status === 'waiting_confirmation') {
      setConfirmData(lastResponse.result.data);
      setState('waiting_confirm');
    } else if (lastResponse.result?.status === 'completed') {
      setState('completed');
    }
  }, [lastResponse]);

  const handleSubmit = () => {
    if (!input.trim()) return;
    send('agent.run', { user_input: input });
    setState('running');
  };

  const handleConfirm = (choice: string) => {
    send('agent.respond', { choice });
    setConfirmData(null);
    setState('running');
  };

  const handleNewConversation = () => {
    // COMPLETED → IDLE: 重置状态开始新对话
    reset();
    setInput('');
    setMessages([]);
    setProgress({ phase: 0, percent: 0 });
    setConfirmData(null);
    setState('idle');
  };

  if (!isConnected) {
    return <Text>正在连接后端...</Text>;
  }

  return (
    <Box flexDirection="column">
      <StatusBar state={state} />
      <MessageList messages={messages} />
      {state === 'idle' && (
        <Box>
          <Text>请输入需求: </Text>
          <TextInput value={input} onChange={setInput} onSubmit={handleSubmit} />
        </Box>
      )}
      {state === 'waiting_confirm' && confirmData && (
        <Dialog
          title={confirmData.title}
          message=""
          options={confirmData.options}
          onSelect={handleConfirm}
          onCancel={() => send('agent.cancel', {})}  // ESC 取消
        />
      )}
      {state === 'running' && <ProgressBar phase={progress.phase} percent={progress.percent} />}
      {state === 'completed' && (
        <Box flexDirection="column">
          <Text bold color="green">对话完成</Text>
          <Text dimColor>输入新需求继续，或按 Ctrl+C 退出</Text>
          <button onClick={handleNewConversation}>开始新对话</button>
        </Box>
      )}
    </Box>
  );
};
```

**Patterns to follow:**
- Hermes Agent 的 React 状态管理模式

**Test scenarios:**
- 状态转换正确
- UI 根据状态正确渲染
- 用户输入正确处理

**Verification:**
- TypeScript 编译通过

---

- [ ] **Unit 6: Python CLI 入口集成**

**Goal:** 修改 Python CLI `tui` 命令以启动 Node.js 前端

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 1, Unit 5

**Files:**
- Modify: `src/ascend_op_agent/cli.py` — 添加 `tui` 命令
- Create: `src/ascend_op_agent/frontend.py` — Node.js 前端启动器
- Create: `tests/test_cli.py` — 更新测试

**Approach:**
```python
@main.command()
@click.option("--local", is_flag=True, help="强制使用本地模式")
@click.pass_context
def tui(ctx: click.Context, local: bool):
    """启动双进程 TUI 模式

    前端基于 Node.js + Ink 构建，后端是独立的 Python 进程。
    支持推理期间的响应式渲染和复杂审批交互。
    """
    config: Config = ctx.obj["config"]

    # 检查 Node.js 是否可用
    if not shutil.which("node"):
        console.print("[red]错误: TUI 模式需要 Node.js[/red]")
        console.print("请安装 Node.js: https://nodejs.org/")
        return

    # 获取前端路径
    frontend_path = Path(__file__).parent.parent / "frontend"
    dist_path = frontend_path / "dist"

    # 检查前端是否已构建
    if not dist_path.exists():
        console.print("[yellow]前端未构建，正在构建...[/yellow]")
        subprocess.run(["npm", "install"], cwd=frontend_path, check=True)
        subprocess.run(["npm", "run", "build"], cwd=frontend_path, check=True)

    # 构建环境变量，传递配置路径
    env = {
        **os.environ,
        "ASCEND_OP_AGENT_CONFIG": str(config.config_path),
        "PYTHONUNBUFFERED": "1",  # 禁用 Python 输出缓冲
    }

    # 启动 Node.js 前端
    try:
        subprocess.run(
            ["node", str(dist_path / "index.js")],
            env=env,
        )
    except Exception as e:
        console.print(f"[red]前端启动失败: {e}[/red]")
```

**Config 传递机制**:
- CLI (`tui` 命令) 读取 Python config 文件路径
- 通过环境变量 `ASCEND_OP_AGENT_CONFIG` 传给 Node.js 前端
- Node.js 前端通过环境变量 `ASCEND_OP_AGENT_CONFIG` 传给 Python 后端
- Python 后端 `backend.py` 读取环境变量初始化 Config

**注意**: 三进程嵌套是设计选择，CLI 作为入口仅负责启动，实际对话在 Node.js 前端和 Python 后端之间进行。

**Patterns to follow:**
- 现有 CLI 命令风格
- 渐进式迁移

**Test scenarios:**
- `ascend-op-agent tui` 成功启动
- Node.js 未安装时错误提示
- 前端未构建时自动构建
- Config 路径正确传递到后端
- 后端能读取配置并初始化

**Verification:**
- `ascend-op-agent tui --help` 正常显示
- 集成测试：前端启动并与后端通信
- Config 集成测试：后端正确读取配置

---

- [ ] **Unit 7: 端到端集成测试**

**Goal:** 验证完整双进程架构的端到端功能

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Units 1-6

**Files:**
- Create: `tests/integration/test_tui_e2e.py` — TUI 端到端测试
- Create: `tests/integration/test_json_rpc_integration.py` — 协议集成测试

**Approach:**
- 启动真实的后端进程
- 通过 pty 模拟终端交互
- 验证 RPC 通信和 UI 渲染

**Test scenarios:**
- 启动 TUI → 后端就绪 → 显示欢迎信息
- 用户输入 → RPC 请求发送 → 后端处理 → 响应显示
- 审批对话框显示 → 用户选择 → 响应发送 → 继续执行
- 取消操作 → 后端取消 → 状态重置
- 后端异常 → 前端错误显示 → 优雅退出

**Verification:**
- `python -m pytest tests/integration/test_tui_e2e.py -v` 通过
- `python -m pytest tests/integration/test_json_rpc_integration.py -v` 通过

---

- [ ] **Unit 8: 文档和迁移指南**

**Goal:** 更新文档，说明双进程架构的使用方式

**Requirements:** N/A

**Dependencies:** Unit 6

**Files:**
- Modify: `docs/architecture.md` — 添加双进程架构说明
- Modify: `README.md` — 添加 TUI 模式说明和 Node.js 依赖说明
- Create: `docs/tui-guide.md` — TUI 使用指南

## System-Wide Impact

### Interaction Graph

| 组件 | 影响 |
|------|------|
| CLI (`cli.py`) | 新增 `tui` 命令，原有 `run` 命令保留 |
| Agent Core | 添加异步接口（`_run_conversation_async`） |
| Backend RPC | 新增 `backend/` 包，不影响现有 Agent 核心逻辑 |
| Frontend | 新增 `frontend/` 目录，与现有代码隔离 |
| Config | 无影响 |

### Error Propagation

- 后端启动失败 → 前端显示错误信息，进程退出
- 后端异常退出 → 前端检测到 EOF，显示错误，优雅退出
- RPC 通信失败 → 前端重试或退出
- LLM 调用失败 → 后端通过 RPC 通知前端，前端显示错误

### State Lifecycle Risks

- **后端僵死**: 设置 RPC 超时，超时后终止后端进程
- **前端崩溃**: 不影响后端，后端检测到 stdin 关闭后可自清理
- **双方僵死**: 用户可通过 SIGINT (Ctrl+C) 中断

## Risks & Dependencies

| 风险 | 影响 | 缓解 |
|------|------|------|
| Node.js 依赖 | 用户需要安装 Node.js | 保留原有 `run` 命令作为备选；文档说明 |
| Windows 兼容性 | Node.js TUI 在 Windows 支持可能有问题 | 测试覆盖；Windows 可用原有 `run` 命令 |
| JSON-RPC 协议兼容性 | 与 Hermes Agent 无法互操作 | 仅内部使用，不要求跨语言兼容 |
| 性能开销 | 双进程比单进程略有开销 | 进程间通信量小，开销可忽略 |
| 调试困难 | 前后端分离，调试复杂 | 添加 `--debug` 选项输出 RPC 通信 |
| 前端构建时间 | 首次启动需要 `npm install + build` | 检测已构建则跳过，显示进度 |

## Alternative Approaches Considered

### Python curses + rich 前端
**拒绝理由**: API 晦涩难懂、组件化困难、无热更新；相比之下 Node.js + Ink 的 React 声明式开发体验更佳

### 单进程异步架构
**拒绝理由**: Python GIL 限制，LLM 调用期间无法真正并行；双进程架构更符合 Hermes 设计，经过验证

### WebSocket 替代 stdin/stdout
**拒绝理由**: 增加复杂度，需要端口管理；stdio 已满足父子进程通信需求

## Phased Delivery

### Phase 1: 后端 RPC 服务（Unit 1）
- Python JSON-RPC 2.0 服务端
- Agent 服务封装
- 单元测试
- **目标**: Python 后端可独立运行，响应 RPC 请求

### Phase 2: 前端脚手架（Unit 2, 3）
- Node.js + TypeScript + Ink 项目初始化
- Ink 组件库开发
- **目标**: 基础 TUI 可显示和交互

### Phase 3: RPC 客户端和应用（Unit 4, 5）
- TypeScript RPC 客户端
- 主应用组件
- **目标**: 前端与后端可通信

### Phase 4: CLI 集成（Unit 6, 7）
- Python CLI `tui` 命令
- 端到端集成测试
- **目标**: `ascend-op-agent tui` 完整可用

### Phase 5: 文档（Unit 8）
- 架构文档
- 使用指南
- **目标**: 用户可正常使用 TUI 模式

## Documentation Plan

| 文档 | 内容 |
|------|------|
| `docs/architecture.md` | 添加双进程架构章节 |
| `docs/tui-guide.md` | TUI 模式使用指南、快捷键说明 |
| `README.md` | 添加 `tui` 命令说明和 Node.js 依赖说明 |

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md](../brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md)
- **Hermes Agent 设计**: 双进程架构、JSON-RPC 2.0、响应式 TUI
- **Ink 文档**: https://github.com/vadimdemedes/ink
- **现有代码**: `src/ascend_op_agent/cli.py`、`src/ascend_op_agent/agent/core.py`
