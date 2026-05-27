---
date: 2026-05-12
topic: agent-progress-notification-system
---

# Agent 进度通知系统

## Summary

为 Ascend Op Agent 引入 Hermes Agent 风格的回调通知机制，使 AIAgent 在推理过程中能够向 TUI 前端推送实时状态更新（thinking、tool 执行、progress），解决用户看不到 Agent 工作状态的问题。

## Problem Frame

当前 AIAgent 的 `run_conversation()` 是一个同步循环，执行过程中**从不发送任何进度通知**。前端只能看到"推理中"状态直到对话完成，用户无法感知 Agent 正在做什么（LLM 思考？工具调用？等待响应？）。参考研究表明 Hermes Agent 和 Claude Code 都通过回调机制实现实时状态推送，而我们的系统虽有 `send_notification()` 基础设施，却从未被调用。

---

## Actors

- A1. **TUI 前端**: Node.js/Ink React 应用，渲染状态栏和进度条，接收 `agent.thinking`、`agent.progress`、`agent.error` 通知
- A2. **JSON-RPC Server**: Python 后端服务，通过 `send_notification()` 向 stdout 发送通知
- A3. **AIAgent**: 核心推理引擎，目前不发送通知，需要改造

---

## Key Flows

- F1. **Agent 推理通知流程**
  - **Trigger:** 用户发送消息，前端进入 `running` 状态
  - **Actors:** TUI 前端 → JSON-RPC Server → AIAgent
  - **Steps:**
    1. 前端发送请求，后端启动 AIAgent 线程池执行
    2. AIAgent 开始 LLM 调用，发送 `{ stage: "thinking" }`
    3. 60 秒无响应时，前端显示"等待响应..."提示
    4. LLM 返回结果，如触发工具调用则发送 `{ stage: "tool_executing", tool_name: "xxx" }`
    5. 工具执行完成，发送 `{ stage: "tool_executing", tool_name: "xxx", success: true }`
    6. 工具执行失败，发送 `{ stage: "tool_executing", tool_name: "xxx", error_code: "...", error_message: "..." }`，Agent 继续
    7. 最终响应返回，发送 `{ stage: "completed" }`，前端切换到 `completed` 状态
  - **Outcome:** 用户在推理过程中看到实时状态变化（思考中 → 执行工具 → 完成），而非长时间"推理中"
  - **Covered by:** R1, R2, R3, R4, R5, R6, R7, R8

---

## Requirements

**[回调接口定义]**

- R1. AIAgent 接受 `progress_callback(tool_progress_callback, status_callback)` 参数，在工具执行和状态变化时调用
- R2. `tool_progress_callback(event_type, name, preview, args)` 支持事件类型：`tool.started`、`tool.complete`、`tool.error`
- R3. `status_callback(kind, text)` 支持状态类型：`thinking`（LLM 推理中）、`idle`（空闲）、`error`（错误）

**[通知转发]**

- R4. JSON-RPC Server 在收到 Agent 回调时，通过 `send_notification()` 将事件转发给前端
- R5. 通知格式为三个阶段：`thinking`（LLM 推理中）、`tool_executing`（工具执行中）、`completed`（完成）。每个阶段携带 `stage` 字段和可选的 `tool_name`/`error_code`/`error_message`

**[前端渲染]**

- R6. TUI 前端解析通知并显示当前阶段：显示"思考中..."→"正在执行 {tool_name}..."→"完成"
- R7. LLM 调用超过 60 秒未返回时，前端显示"等待响应..."提示，Agent 侧由回调超时机制触发
- R8. 工具执行失败时，前端显示 `⚠️ {tool_name} - {error_message}`，Agent 继续执行不阻断

**[会话持久化]**

- R9. 通知事件（thinking、tool 调用）作为 ToolEntry 记录到 SessionRecordManager，保持与现有会话记录体系一致

---

## Acceptance Examples

- AE1. **Covers R1, R2, R3, R5.** 给 AIAgent 传入回调函数后，当 LLM 开始推理时，发送 `{ stage: "thinking" }`；工具执行时发送 `{ stage: "tool_executing", tool_name: "read_file" }`；工具完成时发送 `{ stage: "tool_executing", tool_name: "read_file" }` 带 success 标志

- AE2. **Covers R6, R8.** 用户发送"用ascendc开发一个layernorm算子"后，前端依次显示：*"思考中..."* → *"正在执行 plan_code..."* → *"⚠️ execute_command - 命令执行失败: 无权限"* → Agent 自行恢复继续 → 最终响应

- AE3. **Covers R7.** LLM 调用 60 秒未返回，前端显示"等待响应..."提示用户，Agent 继续后台等待

---

## Success Criteria

- 用户在 Agent 推理过程中能看到实时状态变化，不再面对长时间静止的"推理中"提示
- 工具执行时能看到工具名称和执行结果（成功/失败），提升透明度和信任感
- 超时时有明确提示，不让用户困惑是否卡住
- 会话记录包含完整的推理过程（thinking、tool 调用），便于回溯调试

---

## Scope Boundaries

- 不改变前端技术栈（TUI 继续使用 Node.js/Ink）
- 不改变现有 JSON-RPC 通信协议（继续使用 stdout/stdin）
- 不改变 AIAgent 的核心推理逻辑（只添加回调触发点）
- 不实现流式输出（token-by-token 显示），那是独立的功能

---

## Key Decisions

- **采用回调注入而非集中状态轮询**: 与 Hermes Agent 架构一致，改动最小，实时性最好
- **回调参数从 Agent 传入而非全局注册**: 避免单例陷阱，支持多会话并发
- **通知仅通过现有 stdout 通道转发**: 不引入额外的 WebSocket 或进程间通信机制
- **三阶段模型（thinking → tool_executing → completed）**: 简洁明了，覆盖主要状态
- **60 秒超时检测**: 由 Agent 回调触发，前端显示"等待响应..."提示
- **工具失败不阻断**: 前端显示警告信息 + 错误详情，Agent 继续执行

---

## Dependencies / Assumptions

- D1. `JSONRPCServer.send_notification()` 已在 `backend/rpc/server.py` 实现且可用
- D2. TUI 前端的 `useRPC.ts` 能正确解析和渲染 `agent.progress` 通知（已验证）
- D3. `AgentAsyncWrapper` 可以传递回调函数到 AIAgent

---

## Outstanding Questions

### Resolve Before Planning

- [无阻塞性问题]

### Deferred to Planning

- [技术] 通知事件是否需要持久化到会话记录（作为 ToolEntry）？当前 R9 提及但细节待定
- [技术] Agent 超时回调的具体实现位置：在 LLMClient.call() 内部还是外部触发？

### From Review (2026-05-26)

**P0 Root Cause Resolved — Hermes Architecture Reference**

评审发现 P0 级架构矛盾（"同步循环无法在 LLM 调用期间触发回调"）已通过 Hermes Agent 架构解决：

**关键发现：**
1. Hermes 的 `AIAgent` 在 `__init__` 时注册回调（非调用时注入），回调在 ThreadPoolExecutor worker 线程中运行
2. Hermes 使用 `queue.Queue` 作为同步→异步桥接 — worker 线程 `put()` 事件，async 任务 `get()` 消费并调用 `send_notification`
3. 回调不直接调用 async 函数，而是通过队列解耦，避免跨线程 async 问题

**结论：** P0 根因是**可行的**，方案设计正确。实现时需要：
- `AIAgent.__init__` 接受回调参数（而非 run_conversation 注入）
- 引入 `queue.Queue` 中间层作为 thread→async 桥接
- Worker 线程回调将事件放入队列，async 任务消费并调用 `send_notification`

**受此结论影响的评审发现：**
- P0: 同步循环阻塞回调 → 已确认不可作为独立解决，将作为实现约束
- P1: D3 未实现 → 需修改 AIAgent.__init__ 签名
- P1: R9 conflates thinking/ToolEntry → 需重新考虑持久化方案
- P2: 三阶段模型缺少 error 阶段 → 保持现状，error 作为 tool_executing 子状态