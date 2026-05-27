---
title: Agent 进度通知系统
type: feat
status: active
date: 2026-05-27
origin: docs/brainstorms/2026-05-12-agent-progress-notification-requirements.md
---

# Agent 进度通知系统

## Summary

为 Ascend Op Agent 实现 Hermes Agent 风格的进度通知机制。AIAgent 在推理过程中通过 `queue.Queue` 桥接向 TUI 前端推送实时状态（thinking → tool_executing → completed），解决用户长时间只看"推理中"的问题。

---

## Problem Frame

AIAgent 的 `run_conversation()` 是同步循环，执行中从不发送通知，前端只能看到"推理中"直到完成。用户无法感知 Agent 正在做什么（LLM 思考？工具调用？）。Hermes Agent 和 Claude Code 都有实时状态推送，我们有 `send_notification()` 但从未调用。

---

## Requirements

- R1. AIAgent 接受 `progress_callback(tool_progress_callback, status_callback)` 参数
- R2. `tool_progress_callback` 支持 `tool.started`、`tool.complete`、`tool.error`
- R3. `status_callback` 支持 `thinking`、`idle`、`error`
- R4. JSON-RPC Server 通过 `send_notification()` 转发事件
- R5. 三阶段通知格式：`thinking`、`tool_executing`、`completed`
- R6. 前端解析并显示当前阶段（思考中 → 执行中 → 完成）
- R7. 60 秒超时检测，前端显示"等待响应..."
- R8. 工具失败前端显示警告，不阻断 Agent
- R9. 通知事件作为会话记录持久化

**Origin actors:** A1 (TUI 前端), A2 (JSON-RPC Server), A3 (AIAgent)  
**Origin flows:** F1 (Agent 推理通知流程)  
**Origin acceptance examples:** AE1, AE2, AE3

---

## Scope Boundaries

- 不改变前端技术栈（TUI 继续使用 Node.js/Ink）
- 不改变现有 JSON-RPC 通信协议（继续使用 stdout/stdin）
- 不实现流式输出（token-by-token 显示）

---

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/agent/core.py` — AIAgent 核心类，`run_conversation()` 同步循环，`LLMClient.call()` 调用 LLM
- `src/ascend_op_agent/backend/rpc/agent_service.py` — `AgentAsyncWrapper` 用 ThreadPoolExecutor 运行同步 AIAgent
- `src/ascend_op_agent/backend/rpc/server.py` — `JSONRPCServer.send_notification()` 已实现，async 函数
- `frontend/src/App.tsx` — React 状态机，处理 `agent.progress` 通知
- `frontend/src/components/StatusBar.tsx` — 状态栏，显示 AppState 标签
- `frontend/src/components/ProgressBar.tsx` — 进度条，显示 phase/percent

### Hermes Architecture Reference

- Hermes `AIAgent.__init__` 接受回调参数（非注入）
- Hermes `gateway/stream_consumer.py` 使用 `queue.Queue` 作为同步→异步桥接
- Worker 线程 `put()` 事件，async 任务 `get()` 消费并调用 `send_notification`

### Key Technical Decisions

- **回调在 `__init__` 注册，非注入**: 与 Hermes 一致，避免单例陷阱
- **queue.Queue 桥接**: worker 线程放入队列，async 消费，避免跨线程 async 调用
- **三阶段模型**: `thinking` → `tool_executing` → `completed`，error 作为 tool_executing 子状态
- **超时检测**: 在 LLMClient.call() 外部用线程计时，回调触发

---

## Open Questions

### Resolved During Planning

- **Q: 通知格式采用什么？**  
  A: `{ method: "agent.progress", params: { stage: "thinking"|"tool_executing"|"completed", tool_name?: string, error_code?: string, error_message?: string } }`

- **Q: queue.Queue 在哪里初始化，谁拥有？**  
  A: 在 `AgentAsyncWrapper.__init__` 创建，随 `AgentAsyncWrapper` 生命周期。异步消费任务在 `run_conversation_async()` 启动前创建。

- **Q: thinking 事件是否需要持久化？**  
  A: 暂不持久化，仅实时推送。R9 待后续单独处理。

### Deferred to Implementation

- **Q: 60 秒超时计时起点？**  
  A: 从 `LLMClient.call()` 开始计时（外部线程）。实现时需确保跨迭代累加。

---

## Implementation Units

- U1. **回调接口注入 AIAgent**
- U2. **实现 queue.Queue 桥接**
- U3. **在 run_conversation() 中集成回调调用**
- U4. **AgentAsyncWrapper 桥接回调**
- U5. **前端三阶段显示适配**
- U6. **60 秒超时检测机制**

---

- U1. **回调接口注入 AIAgent**

**Goal:** AIAgent 接受回调参数，在关键节点可被调用

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`

**Approach:**
- 在 `AIAgent.__init__` 添加两个可选回调参数：
  - `tool_progress_callback: Optional[Callable[..., None]] = None`
  - `status_callback: Optional[Callable[..., None]] = None`
- 回调签名为纯函数，不依赖 asyncio：
  - `tool_progress_callback(event_type: str, tool_name: str, **kwargs)`
  - `status_callback(kind: str, text: Optional[str] = None)`
- 回调存储为实例变量 `self._tool_progress_callback` 和 `self._status_callback`

**Patterns to follow:**
- 参考 Hermes `agent/agent_init.py` 中 AIAgent 接受回调的方式

**Test scenarios:**
- Happy path: 传入回调时，`run_conversation()` 在各节点调用回调
- Edge case: 回调为 None 时，各调用点做 `if callback:` 防护
- Error path: 回调抛出异常时不影响主流程（try/except 包装）

**Verification:**
- 单元测试验证回调在正确时机被调用，传入正确参数

---

- U2. **实现 queue.Queue 桥接**

**Goal:** 提供线程安全的队列，worker 线程放入事件，async 任务消费并调用 send_notification

**Requirements:** R4, R5

**Dependencies:** None（此模块独立于 AIAgent）

**Files:**
- Create: `src/ascend_op_agent/backend/rpc/notification_queue.py`

**Approach:**
- `NotificationQueue` 类：
  - `__init__`: 创建 `queue.Queue()`
  - `put(event_type: str, params: dict)`: worker 线程调用，放入队列
  - `start_consuming(loop, send_notification_fn)`: 启动后台 async 任务，从队列消费并调用 `send_notification`
  - `stop()`: 停止消费
- `start_consuming` 使用 `asyncio.create_task()` 在事件循环中运行异步消费循环
- 消费循环：`while self._running: event = await loop.run_in_executor(None, self._queue.get)` 然后 `await send_notification(event['type'], event['params'])`

**Technical design:**
```
NotificationQueue
  __init__
    self._queue = queue.Queue()
    self._running = False
  put(event_type, params)
    # called from worker thread
    self._queue.put({'type': event_type, 'params': params})
  start_consuming(loop, send_notification_fn)
    # called from async context
    self._running = True
    asyncio.create_task(self._consume_loop(loop, send_notification_fn))
  _consume_loop
    while self._running:
      event = await loop.run_in_executor(None, self._queue.get)
      await send_notification(event['type'], event['params'])
  stop
    self._running = False
```

**Patterns to follow:**
- Hermes `gateway/stream_consumer.py` 的 queue.Queue 使用模式
- Python `queue.Queue` 文档的 producer/consumer 模式

**Test scenarios:**
- Happy path: put 后 consume_loop 收到正确事件
- Edge case: put 多次，consume 按 FIFO 顺序消费
- Error path: send_notification 抛出异常时记录日志不崩溃

**Verification:**
- 集成测试验证 worker 线程 put 的事件被 async 消费并调用 send_notification

---

- U3. **在 run_conversation() 中集成回调调用**

**Goal:** 在 AIAgent 推理循环的关键节点触发回调

**Requirements:** R1, R2, R3, R5

**Dependencies:** U1

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`

**Approach:**
在 `run_conversation()` 的同步循环中，在以下位置调用回调：

1. **LLM 调用前**（循环开始）:
   ```python
   if self._status_callback:
       self._status_callback("thinking")
   ```

2. **LLM 调用返回后**（line 158 之后）:
   ```python
   if self._status_callback:
       self._status_callback("idle")  # LLM 返回，thinking 结束
   ```

3. **工具执行前**（`_execute_tool_call` 或 `_execute_tool_call_from_result` 内部，line 296 和 line 375 附近）:
   ```python
   if self._tool_progress_callback:
       self._tool_progress_callback("tool.started", tool_name, ...)
   ```

4. **工具执行成功后**（同上位置）:
   ```python
   if self._tool_progress_callback:
       self._tool_progress_callback("tool.complete", tool_name, success=True, ...)
   ```

5. **工具执行失败后**:
   ```python
   if self._tool_progress_callback:
       self._tool_progress_callback("tool.error", tool_name, error_code=..., error_message=...)
   ```

6. **最终响应返回前**（循环退出时）:
   ```python
   if self._status_callback:
       self._status_callback("completed")
   ```

**Patterns to follow:**
- Hermes `run_agent.py` 中回调触发时机

**Test scenarios:**
- Happy path: 每个阶段回调被调用
- Edge case: 快速返回（无工具调用）时 `tool.started` 不应触发
- Error path: 回调抛出异常时主流程继续

**Verification:**
- 集成测试验证每个节点回调参数正确

---

- U4. **AgentAsyncWrapper 桥接回调**

**Goal:** 创建 NotificationQueue，实例化带回调的 AIAgent，启动队列消费

**Requirements:** R4

**Dependencies:** U2, U3

**Files:**
- Modify: `src/ascend_op_agent/backend/rpc/agent_service.py`

**Approach:**
- `AgentAsyncWrapper.__init__`:
  1. 创建 `self._notification_queue = NotificationQueue()`
  2. 创建 `functools.partial(tool_callback, queue.put)` 和 `functools.partial(status_callback, queue.put)`
  3. 实例化 AIAgent 时传入这两个 partial 作为回调
  4. 保存 `send_notification` 函数引用

- `run_conversation_async()`:
  1. 在 `await loop.run_in_executor(...)` 之前：`await self._notification_queue.start_consuming(loop, self._send_notification)`
  2. 执行后：`await self._notification_queue.stop()`

**Patterns to follow:**
- Hermes `tui_gateway/server.py` 中 `_agent_cbs()` 创建回调和 `tui_gateway/transport.py` 的传输桥接

**Test scenarios:**
- Happy path: 回调触发 → 队列 → send_notification 被调用
- Edge case: 多会话并发（每个 AgentAsyncWrapper 独立队列）
- Error path: 队列满时 put() 阻塞策略（设置 maxsize）

**Verification:**
- 端到端测试：调用后检查 stdout 收到正确通知序列

---

- U5. **前端三阶段显示适配**

**Goal:** 前端正确解析和显示三阶段通知

**Requirements:** R6, R7, R8

**Dependencies:** U4（后端完成后才能测前端）

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/StatusBar.tsx`
- Modify: `frontend/src/components/ProgressBar.tsx`

**Approach:**

1. **App.tsx** — `agent.progress` 处理更新：
   ```typescript
   // params: { stage: 'thinking' | 'tool_executing' | 'completed', tool_name?: string, error_code?: string, error_message?: string }
   // 更新 progress state
   setProgress({ stage: params.stage, tool_name: params.tool_name, error_code: params.error_code })
   ```

2. **ProgressBar.tsx** — 支持 stage 显示：
   ```typescript
   interface ProgressBarProps {
     label?: string;
     percent: number;
     stage?: 'thinking' | 'tool_executing' | 'completed';
     tool_name?: string;
     error_message?: string;
   }
   ```
   显示逻辑：
   - `stage === 'thinking'`: 显示"思考中..."
   - `stage === 'tool_executing' && error_message`: 显示 `⚠️ {tool_name} - {error_message}`
   - `stage === 'tool_executing' && !error_message`: 显示"正在执行 {tool_name}..."
   - `stage === 'completed'`: 显示"完成"

3. **StatusBar.tsx** — 无需修改，因为 running 状态已覆盖 thinking 和 tool_executing

**Patterns to follow:**
- 现有 `agent.progress` 处理模式

**Test scenarios:**
- Happy path: 收到 `{ stage: "thinking" }` 显示"思考中..."
- Happy path: 收到 `{ stage: "tool_executing", tool_name: "read_file" }` 显示"正在执行 read_file..."
- Error path: 收到 `{ stage: "tool_executing", error_message: "无权限" }` 显示警告图标

**Verification:**
- 前端单元测试验证各 stage 渲染正确

---

- U6. **60 秒超时检测机制**

**Goal:** LLM 调用 60 秒无返回时触发超时回调

**Requirements:** R7

**Dependencies:** U1（回调机制已就绪）

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`

**Approach:**
在 `LLMClient.call()` 外部实现，不修改 `LLMClient` 本身：

在 `run_conversation()` 的 LLM 调用前启动计时线程（使用 `threading.Timer(60, callback)`），LLM 返回后取消计时器：

```python
import threading

class TimeoutTracker:
    def __init__(self, timeout, callback):
        self._timeout = timeout
        self._callback = callback
        self._timer = None
        
    def start(self):
        self._timer = threading.Timer(self._timeout, self._callback)
        self._timer.start()
        
    def cancel(self):
        if self._timer:
            self._timer.cancel()
```

在 `run_conversation()` 中：
```python
timeout_tracker = TimeoutTracker(60, lambda: self._status_callback("waiting"))
timeout_tracker.start()
try:
    response = self._llm_client.call(...)
finally:
    timeout_tracker.cancel()
```

**Patterns to follow:**
- Hermes 的超时检测模式（在 LLM 调用外部计时）

**Test scenarios:**
- Happy path: LLM 60 秒内返回，计时器被取消
- Edge case: LLM 超时（>60s），`status_callback("waiting")` 被调用
- Error path: 连续多次 LLM 调用超时，计时器正确重置

**Verification:**
- Mock LLM 调用延迟 65 秒，验证超时回调触发

---

## System-Wide Impact

- **Interaction graph:** `AIAgent.__init__` 新增回调参数；`AgentAsyncWrapper` 创建 `NotificationQueue`；`JSONRPCServer` 发送通知到 stdout
- **Error propagation:** 回调抛异常不影响主流程；队列满时 put() 阻塞；send_notification 异常记录日志
- **State lifecycle risks:** 队列消费任务在 `stop()` 时正确退出；多会话并发时各队列独立
- **API surface parity:** 不改变 AIAgent.run_conversation() 签名（回调在 `__init__`）
- **Integration coverage:** 端到端测试需验证：请求 → 回调触发 → 队列 → send_notification → stdout → 前端渲染

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 跨线程 async 调用 | queue.Queue 桥接，worker `put()`，async `get()` |
| 队列满导致 worker 阻塞 | `queue.Queue(maxsize=100)`，队列满时 put() 阻塞 |
| 前端渲染阻塞 | 前端已验证 useRPC.ts 可正确解析通知 |
| 超时计时器泄漏 | `try/finally` 确保 `cancel()` 被调用 |

---

## Documentation / Operational Notes

- 更新 CLAUDE.md 中的 Agent 执行流程描述，添加通知机制说明
- TUI 前端需在收到首个 `agent.progress` 后才显示进度（避免闪烁）

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-12-agent-progress-notification-requirements.md](docs/brainstorms/2026-05-12-agent-progress-notification-requirements.md)
- Hermes architecture: `queue.Queue` 桥接模式参考 `gateway/stream_consumer.py`
- 现有基础设施: `backend/rpc/server.py:58-72 send_notification()` 已实现