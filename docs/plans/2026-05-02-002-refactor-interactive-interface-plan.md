---
title: "refactor: Ascend Op Agent 交互界面双进程架构重构"
type: refactor
status: active
date: 2026-05-02
origin: "docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md"
---

# Ascend Op Agent 交互界面双进程架构重构规划

## Overview

将 Ascend Op Agent 的交互界面从当前的单进程 CLI 模式重构为 Hermes Agent 风格的双进程架构：前端基于 TUI（可能是 Node.js/Ink 或 Python rich-curses），后端是承载完整 Agent 引擎的 Python 子进程，两者通过 **stdin/stdout 管道上的 newline-delimited JSON-RPC 2.0** 协议进行全双工通信。

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
- TUI 前端实现（基于 Python curses + rich）
- 后端 Agent 引擎的 JSON-RPC 服务封装
- 协议层集成测试

### Out of Scope
- Node.js/Ink 前端实现（保持 Python TUI）
- Ink React 组件移植
- 现有 Agent 引擎逻辑修改（仅重新封装）
- MCP 服务器和 Skill 仓库功能修改

## Key Technical Decisions

### KD-1: 采用 Python TUI 而非 Node.js/Ink
**决策**: 前端使用 Python curses + rich 构建 TUI，不引入 Node.js
**理由**: 项目技术栈为纯 Python，避免引入额外运行时；curses 跨平台兼容性好
**替代考虑**: Node.js/Ink 可实现更复杂的动画效果，但增加构建复杂度

### KD-2: 使用 json-rpc 库实现协议层
**决策**: Python 使用 `json-rpc` 库（或手动实现简单版）处理 JSON-RPC 2.0
**理由**: 轻量、无复杂依赖，与 Hermes Agent 的协议格式兼容
**替代考虑**: 自定义简单协议（更轻量但不够标准）

### KD-3: 子进程管理模式
**决策**: 使用 `subprocess.Popen` 管理 Python 后端进程，前端为主进程
**理由**: 简单直接，便于控制后端生命周期；前端崩溃不影响后端
**替代考虑**: 后端为主进程（更符合 Hermes 原始设计，但前端控制权较少）

### KD-4: 渐进式重构
**决策**: 保留现有 CLI 入口，新增 `tui` 命令作为双进程模式入口
**理由**: 允许用户选择使用旧模式（单进程）或新模式（双进程），降低迁移风险
**替代考虑**: 直接替换（风险较高）

## High-Level Technical Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        TUI Frontend (Frontend)                      │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  主进程 (Main Process)                                     │  │
│  │  ├── InputHandler: 捕获用户键盘输入                       │  │
│  │  ├── Renderer: 基于 curses + rich 的响应式渲染            │  │
│  │  ├── StateManager: 跟踪 Agent 状态（idle/running/waiting）│  │
│  │  └── RPCClient: JSON-RPC 2.0 客户端（stdin/stdout）      │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ stdin/stdout (JSON-RPC 2.0)
                            │ newline-delimited JSON
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Agent Backend (Backend)                         │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  子进程 (Child Process)                                    │  │
│  │  ├── RPCServer: JSON-RPC 2.0 服务端（stdin/stdout）       │  │
│  │  ├── AIAgent: 核心 Agent 引擎（已有）                     │  │
│  │  ├── WorkflowEngine: 工作流引擎（已有）                   │  │
│  │  └── ToolRegistry: 工具注册（已有）                       │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
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
       │  CANCELLED   │
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

- [ ] **Unit 1: JSON-RPC 2.0 协议层**

**Goal:** 实现 JSON-RPC 2.0 的客户端和服务端封装

**Requirements:** R-NEW1

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/rpc/__init__.py`
- Create: `src/ascend_op_agent/rpc/protocol.py` — JSON-RPC 2.0 核心实现
- Create: `src/ascend_op_agent/rpc/client.py` — RPC 客户端封装
- Create: `src/ascend_op_agent/rpc/server.py` — RPC 服务端封装
- Create: `tests/unit/rpc/test_protocol.py`
- Create: `tests/unit/rpc/test_client.py`
- Create: `tests/unit/rpc/test_server.py`

**Approach:**
- 手动实现简单版 JSON-RPC 2.0（符合规范，轻量无依赖）
- 支持请求/响应/通知三种消息类型
- 使用 `json.loads()` + `json.dumps()` 处理 newline-delimited JSON
- stdin/stdout 作为传输层

**Patterns to follow:**
- Hermes Agent 的 JSON-RPC over stdio 格式
- JSON-RPC 2.0 规范（jsonrpc.org）

**Test scenarios:**
- 解析有效 JSON-RPC 请求
- 生成有效 JSON-RPC 响应
- 处理通知消息（无响应）
- 处理无效 JSON（返回错误）
- 并发请求与响应匹配

**Verification:**
- `python -m pytest tests/unit/rpc/ -v` 通过
- 协议兼容 Hermes Agent（可与之通信测试）

---

- [ ] **Unit 2: TUI 前端渲染器**

**Goal:** 实现基于 curses + rich 的响应式 TUI 前端

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/tui/__init__.py`
- Create: `src/ascend_op_agent/tui/renderer.py` — 渲染器基类
- Create: `src/ascend_op_agent/tui/components.py` — 常用组件（对话框、进度条）
- Create: `src/ascend_op_agent/tui/screen.py` — 屏幕管理
- Create: `src/ascend_op_agent/tui/input.py` — 输入处理
- Create: `tests/unit/tui/test_renderer.py`
- Create: `tests/unit/tui/test_components.py`

**Approach:**
- curses 用于底层终端控制（光标位置、清除屏幕）
- rich 用于富文本渲染（颜色、粗体等）
- 双缓冲渲染避免闪烁
- 独立输入线程（非阻塞键盘捕获）

**Components 设计**:
```python
class Component(ABC):
    @abstractmethod
    def render(self, screen) -> None: ...
    @abstractmethod
    def handle_input(self, key) -> Optional[InputResult]: ...

class ConfirmationDialog(Component):
    """确认对话框"""
    def __init__(self, title: str, message: str, options: list[str]):
        self.selected = 0
        self.options = options

    def render(self, screen):
        # 使用 rich Panel 渲染对话框
        # 高亮当前选项
        pass

    def handle_input(self, key):
        if key == curses.KEY_UP:
            self.selected = max(0, self.selected - 1)
        elif key == curses.KEY_DOWN:
            self.selected = min(len(self.options) - 1, self.selected + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            return InputResult(confirmed=True, choice=self.options[self.selected])

class ProgressBar(Component):
    """进度条"""
    def __init__(self, phase: str, percent: float):
        self.phase = phase
        self.percent = percent

    def render(self, screen):
        # 使用 rich Progress 渲染进度条
        bar = "█" * int(self.percent * 40)
        screen.addstr(f"{self.phase}: [{bar:<40}] {int(self.percent * 100)}%")
```

**Patterns to follow:**
- Hermes Agent 的 TUI 响应式渲染模式
- 现有 `InteractiveSelector` 的 curses 使用方式

**Test scenarios:**
- 渲染确认对话框
- 渲染进度条
- 键盘导航（上下左右）
- Enter 确认选择
- ESC 取消操作

**Verification:**
- `python -m pytest tests/unit/tui/ -v` 通过
- TUI 在终端可正常显示和交互

---

- [ ] **Unit 3: TUI 状态管理器**

**Goal:** 实现前端状态管理和状态机转换

**Requirements:** R-NEW1, R-NEW2

**Dependencies:** Unit 2

**Files:**
- Create: `src/ascend_op_agent/tui/state.py` — 状态定义和转换
- Create: `src/ascend_op_agent/tui/app.py` — 主应用类
- Create: `tests/unit/tui/test_state.py`

**Approach:**
```python
class AppState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_CONFIRM = "waiting_confirm"
    WAITING_INPUT = "waiting_input"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"

class TUISession:
    def __init__(self, rpc_client: RPCClient):
        self.state = AppState.IDLE
        self.rpc_client = rpc_client
        self.pending_request_id: Optional[int] = None
        self.pending_confirm: Optional[dict] = None
        self.message_history: list[Message] = []

    def handle_rpc_response(self, response: RPCResponse):
        if response.is_error():
            self.set_state(AppState.ERROR)
        elif response.result.get("status") == "waiting_confirmation":
            self.pending_confirm = response.result["data"]
            self.set_state(AppState.WAITING_CONFIRM)
        elif response.result.get("status") == "completed":
            self.set_state(AppState.COMPLETED)

    def handle_rpc_notification(self, notification: RPCNotification):
        if notification.method == "agent.thinking":
            self.add_message(notification.params["message"])
        elif notification.method == "agent.progress":
            self.update_progress(notification.params["phase"],
                                 notification.params["percent"])
```

**Patterns to follow:**
- Hermes Agent 的状态管理模式

**Test scenarios:**
- IDLE → RUNNING 转换
- RUNNING → WAITING_CONFIRM 转换
- WAITING_CONFIRM → RUNNING 转换（用户确认）
- 取消操作的状态转换
- 错误状态处理

**Verification:**
- `python -m pytest tests/unit/tui/test_state.py -v` 通过

---

- [ ] **Unit 4: 后端 Agent RPC 服务封装**

**Goal:** 将现有 AIAgent 封装为 JSON-RPC 服务端

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 1, Unit 2（需要理解接口契约）

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py` — 添加 RPC 服务接口
- Create: `src/ascend_op_agent/rpc/agent_server.py` — Agent RPC 服务封装
- Create: `tests/unit/rpc/test_agent_server.py`

**Approach:**
```python
class AgentRPCService:
    """Agent JSON-RPC 服务封装"""

    def __init__(self, agent: AIAgent):
        self.agent = agent
        self._request_handlers = {
            "agent.run": self._handle_run,
            "agent.cancel": self._handle_cancel,
            "agent.respond": self._handle_respond,
            "session.reset": self._handle_reset,
            "session.status": self._handle_status,
        }

    def _handle_run(self, params: dict) -> dict:
        """处理用户输入，启动 Agent 对话"""
        user_input = params.get("user_input")
        # 异步启动（不阻塞）
        asyncio.create_task(self._run_conversation_async(user_input))
        return {"status": "running"}

    async def _run_conversation_async(self, user_input: str):
        """异步运行对话，发送进度通知"""
        self._send_notification("agent.thinking", {"message": "正在处理..."})

        # 分阶段发送进度
        for phase, percent in self._track_progress():
            self._send_notification("agent.progress", {
                "phase": phase,
                "percent": percent
            })

        # 等待 LLM 响应
        response = await self.agent.run_conversation_async(user_input)

        if response.requires_confirmation():
            self._send_result(self._request_id, {
                "status": "waiting_confirmation",
                "data": response.confirmation_data()
            })
        else:
            self._send_result(self._request_id, {
                "status": "completed",
                "response": response.text
            })

    def _handle_cancel(self, params: dict) -> dict:
        """取消当前操作"""
        self.agent.cancel()
        return {"status": "cancelled"}

    def _handle_respond(self, params: dict) -> dict:
        """处理用户响应（确认/拒绝/修改）"""
        choice = params.get("choice")
        self.agent.submit_response(choice)
        return {"status": "running"}
```

**Patterns to follow:**
- Hermes Agent 的 `_mcp_loop` daemon 线程模式
- 异步任务管理（asyncio）

**Test scenarios:**
- RPC 方法调度正确
- 异步通知发送
- 取消操作生效
- 错误处理

**Verification:**
- `python -m pytest tests/unit/rpc/test_agent_server.py -v` 通过

---

- [ ] **Unit 5: 子进程启动器**

**Goal:** 实现前端启动和管理后端子进程的逻辑

**Requirements:** R-NEW1

**Dependencies:** Unit 1, Unit 4

**Files:**
- Create: `src/ascend_op_agent/tui/launcher.py` — 子进程启动和管理
- Create: `tests/unit/tui/test_launcher.py`

**Approach:**
```python
class BackendLauncher:
    """后端子进程启动器"""

    def __init__(self, config: Config):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.rpc_client: Optional[RPCClient] = None

    def start(self) -> RPCClient:
        """启动后端进程并返回 RPC 客户端"""
        # 构建后端启动命令
        cmd = [
            sys.executable,  # 当前 Python 解释器
            "-m", "ascend_op_agent.backend",  # 后端入口
            "--config", self.config.config_path,
        ]

        # 启动子进程
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # stderr 可选用于调试
            text=True,
            bufsize=1,  # 行缓冲
        )

        # 创建 RPC 客户端
        self.rpc_client = RPCClient(
            stdin=self.process.stdin,
            stdout=self.process.stdout,
        )

        # 等待后端就绪
        if not self._wait_ready(timeout=10):
            raise RuntimeError("Backend failed to start")

        return self.rpc_client

    def _wait_ready(self, timeout: float) -> bool:
        """等待后端发送就绪通知"""
        start = time.time()
        while time.time() - start < timeout:
            # 从 stdout 读取响应
            line = self.process.stdout.readline()
            if not line:
                # 子进程已退出
                return False
            try:
                msg = json.loads(line)
                if msg.get("method") == "backend.ready":
                    return True
            except json.JSONDecodeError:
                continue
        return False

    def shutdown(self):
        """关闭后端进程"""
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
```

**Patterns to follow:**
- Hermes Agent 的主进程/子进程架构

**Test scenarios:**
- 成功启动后端进程
- 后端启动失败处理
- 优雅关闭（terminate）
- 强制关闭（kill）

**Verification:**
- `python -m pytest tests/unit/tui/test_launcher.py -v` 通过

---

- [ ] **Unit 6: TUI 命令入口**

**Goal:** 添加 `ascend-op-agent tui` 命令作为双进程模式入口

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Unit 3, Unit 5

**Files:**
- Modify: `src/ascend_op_agent/cli.py` — 添加 `tui` 命令
- Create: `src/ascend_op_agent/tui/__main__.py` — TUI 主入口
- Create: `tests/test_cli.py` — 更新测试

**Approach:**
```python
@main.command()
@click.option("--local", is_flag=True, help="强制使用本地模式")
@click.pass_context
def tui(ctx: click.Context, local: bool):
    """启动双进程 TUI 模式

    前端基于 curses + rich 构建，后端是独立的 Python 进程。
    支持推理期间的响应式渲染和复杂审批交互。
    """
    config: Config = ctx.obj["config"]

    # 启动后端
    launcher = BackendLauncher(config)
    try:
        rpc_client = launcher.start()
    except RuntimeError as e:
        console.print(f"[red]后端启动失败: {e}[/red]")
        return

    # 运行 TUI
    app = TUISession(rpc_client)
    app.run()
```

**Patterns to follow:**
- 现有 CLI 命令风格
- 保留原有 `run` 命令（单进程模式）

**Test scenarios:**
- `ascend-op-agent tui` 成功启动
- 后端启动失败时错误提示
- 优雅退出

**Verification:**
- `ascend-op-agent tui --help` 正常显示
- 集成测试：TUI 启动并可与后端通信

---

- [ ] **Unit 7: 端到端集成测试**

**Goal:** 验证完整双进程架构的端到端功能

**Requirements:** R-NEW1, R-NEW2, R-NEW3

**Dependencies:** Units 1-6

**Files:**
- Create: `tests/integration/test_tui_e2e.py` — TUI 端到端测试
- Create: `tests/integration/test_json_rpc_integration.py` — 协议集成测试

**Approach:**
- 使用 pty 模拟终端
- 启动真实的后端子进程
- 模拟用户输入（键盘事件）
- 验证渲染输出和 RPC 通信

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
- Modify: `README.md` — 添加 TUI 模式说明
- Create: `docs/tui-guide.md` — TUI 使用指南

**Approach:**
- 架构文档：描述前后端职责划分、协议设计
- 使用指南：TUI 模式启动方式、快捷键说明
- 迁移指南：从旧 `run` 命令迁移到 `tui` 命令

## System-Wide Impact

### Interaction Graph

| 组件 | 影响 |
|------|------|
| CLI (`cli.py`) | 新增 `tui` 命令，原有 `run` 命令保留 |
| Agent Core | 添加异步接口（`_run_conversation_async`） |
| RPC Layer | 新增 `rpc/` 包，不影响现有代码 |
| TUI Layer | 新增 `tui/` 包，与现有代码隔离 |
| Config | 无影响 |

### Error Propagation

- 后端启动失败 → 前端显示错误信息，进程退出
- 后端异常退出 → 前端检测到 EOF，显示错误，优雅退出
- RPC 通信失败 → 前端重试或退出
- LLM 调用失败 → 后端通过 RPC 通知前端，前端显示错误

### State Lifecycle Risks

- **后端僵死**: 设置 RPC 超时，超时后终止后端进程
- **前端崩溃**: 不影响后端，后端检测到 stdout 关闭后可自清理
- **双方僵死**: 用户可通过 SIGINT (Ctrl+C) 中断

## Risks & Dependencies

| 风险 | 影响 | 缓解 |
|------|------|------|
| curses 跨平台兼容性 | macOS/Linux 支持，Windows 可能有问题 | 测试覆盖，Windows 回退到纯文本 |
| JSON-RPC 协议兼容性 | 与 Hermes Agent 无法互操作 | 仅内部使用，不要求跨语言兼容 |
| 性能开销 | 双进程比单进程略有开销 | 进程间通信量小，开销可忽略 |
| 调试困难 | 前后端分离，调试复杂 | 添加 `--debug` 选项输出 RPC 通信 |

## Alternative Approaches Considered

### Node.js/Ink 前端
**拒绝理由**: 增加构建复杂度，需要 Node.js 运行时；Python curses + rich 已足够满足需求

### 单进程异步架构
**拒绝理由**: Python GIL 限制，LLM 调用期间无法真正并行；双进程架构更符合 Hermes 设计

### WebSocket 替代 stdin/stdout
**拒绝理由**: 增加复杂度，需要端口管理；stdio 已满足父子进程通信需求

## Phased Delivery

### Phase 1: 协议层（Unit 1）
- JSON-RPC 2.0 核心实现
- 单元测试覆盖
- **目标**: 协议可用，可与 Hermes Agent 通信

### Phase 2: 前端基础（Unit 2, 3）
- TUI 渲染器
- 状态管理
- 组件库
- **目标**: 基础 TUI 可显示和交互

### Phase 3: 后端封装（Unit 4, 5）
- Agent RPC 服务封装
- 子进程启动器
- **目标**: 后端可启动并响应 RPC 请求

### Phase 4: 集成和入口（Unit 6, 7）
- TUI 命令入口
- 端到端测试
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
| `README.md` | 添加 `tui` 命令说明 |

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md](../brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md)
- **Hermes Agent 设计**: 双进程架构、JSON-RPC 2.0、响应式 TUI
- **现有代码**: `src/ascend_op_agent/cli.py`、`src/ascend_op_agent/agent/core.py`
