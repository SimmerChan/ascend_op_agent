---
title: "feat: Session 完整对话记录重构 (参照 Claude Code 设计)"
type: refactor
status: active
date: 2026-05-08
origin: docs/plans/2026-05-07-001-refactor-session-complete-conversation-record-plan.md
deepened: 2026-05-08
---

# Session 完整对话记录重构 (参照 Claude Code 设计)

## Summary

将当前仅存储 `user/assistant` 消息对的 `_conversation_history` 重构为完整的会话记录系统，采用 **JSONL Append-only 格式**（参照 Claude Code 的 `sessionStorage.ts` 设计），完整捕获用户输入、LLM 原始输入（system prompt + conversation history）、LLM 文本响应、工具调用请求/响应。数据结构包含完整元数据（时间戳、模型信息、Token 用量、迭代次数等），支持后续对话可视化和分析。

## Problem Frame

当前 `AIAgent._conversation_history` 仅存储 `{"role": "user", "content"}` 和 `{"role": "assistant", "content"}` 两种消息，缺失：
- 发送给 LLM 的完整 system prompt
- LLM API 的原始输入/输出
- 工具调用的请求参数和执行结果
- 会话元数据（session_id、创建时间、迭代次数等）
- **Token 用量**（无法进行成本分析）
- **每条消息的独立时间戳**（无法渲染精确时间线）
- **Append-only 写入**（现有 JSON 全量覆盖有数据丢失风险）

这导致无法：
1. 调试 LLM 行为时查看完整的上下文
2. 分析工具调用失败原因
3. 在会话重启后恢复完整对话历史
4. 审计 Agent 的决策过程
5. 进行 Token 成本分析
6. 支持高频写入场景（Append-only 避免文件锁）

## Reference Architecture: Claude Code Session Design

| 特性 | Claude Code 实现 | 本方案对应设计 |
|------|----------------|--------------|
| 存储格式 | JSONL（每行一个 Entry） | JSONL Append-only |
| 写入机制 | 100ms 批量聚合 | 100ms 批量聚合写入缓冲 |
| 大文件处理 | 100MB 分块 | 分块写入（MAX_CHUNK_BYTES） |
| 消息时间戳 | 每条消息独立 timestamp | 每条 Entry 独立 timestamp |
| Token 追踪 | cost-tracker.ts 独立模块 | TokenRecord 嵌入 LLMRecord |
| 消息链 | parentUuid 形成树结构 | Entry ID 链（V2 支持分支） |
| 压缩摘要 | CompactSummary 组件 | Deferred to V2 |

## Requirements

### Data Model
- R1. Session 数据结构包含：session_id、Entry 列表（用户输入、LLM 输入输出、工具调用）、完整元数据
- R2. 每条 Entry 包含独立 timestamp、Entry ID，支持时间线渲染
- R3. LLMRecord 包含 input_tokens、output_tokens 估算，支持成本分析

### Persistence & Recovery
- R4. 采用 JSONL Append-only 格式，避免文件锁和覆盖丢失
- R5. 批量写入缓冲（100ms FLUSH_INTERVAL_MS），大文件分块（MAX_CHUNK_BYTES）
- R6. 会话记录持久化到 `~/.ascend_op_agent/sessions/<session_id>.jsonl`

### Query Interface
- R7. 提供 RPC 方法 `session.get_history` 查询完整会话记录
- R8. 支持按 session_id 读取指定会话或获取最近 N 条

### Backward Compatibility
- R9. 现有 `reset_conversation()` 行为保持不变（仅重置内存状态）

## Scope Boundaries

- 不修改 `MemoryStore` 的现有行为（pool-based memory）
- 不修改 `EpisodicMemory` 的向量检索功能（保持独立）
- CLI/TUI 界面保持不变

### Deferred to Follow-Up Work

- 向量相似会话检索功能（后续集成 `EpisodicMemory.search_similar_episodes`）
- 消息链 ID 和分支支持（V2 实现 `parentUuid` 类似机制）
- 上下文压缩摘要功能（V2 实现 `CompactSummary` 类似机制）
- Token 精确统计（需 LLM Adapter 返回 usage 信息）

## Context & Research

### Relevant Code and Patterns

- `agent/core.py:67` — `_conversation_history` 仅存储 user/assistant 消息对
- `agent/core.py:96-99` — LLM 调用入口，`call()` 方法接收 system_prompt 和 history
- `agent/core.py:154` — 工具执行 `tool.execute(**args)` 位置
- `backend.py:157-158` — RPC 方法注册，`session.reset` 已存在但无 `session.get_history`
- `agent/providers/` — LLM adapter 的 `complete()` 方法仅返回文本响应

### Institutional Learnings

- **Claude Code Session 设计**（参考 `src/utils/sessionStorage.ts`）：
  - JSONL Append-only 格式
  - 批量写入 + 分块机制
  - 每条消息独立 timestamp
- 参考 Hermes Agent 的 MemoryStore 设计（`agent/memory.py:17`）
- 7 层 Prompt 设计在 `PromptBuilder` 中实现（`agent/prompt_builder.py`）

### External References

- Claude Code `src/types/logs.ts` — `LogOption` 类型定义
- Claude Code `src/utils/sessionStorage.ts` — `Project` 类批量写入实现

## Key Technical Decisions

1. **JSONL Append-only 格式**：参照 Claude Code `sessionStorage.ts`，每行一个 Entry，append-only 特性适合高频写入，避免文件锁竞争，支持大文件分块写入。

2. **批量写入缓冲**：100ms `FLUSH_INTERVAL_MS` 聚合写入，减少 I/O 次数。缓冲在 `shutdown()` 时确保清空。

3. **每条 Entry 独立时间戳**：每个 LLM 调用、工具调用都有独立 timestamp，支持精确时间线渲染和耗时分析。

4. **Token 用量追踪**：`LLMRecord` 包含 `input_tokens`、`output_tokens` 估算字段（V1 使用字符数/4 估算，V2 集成精确统计）。

5. **新建 `SessionRecordManager` 而非复用 `EpisodicMemory`**：后者设计用于向量相似性检索，不适合 Append-only JSONL 写入模式。SessionRecordManager 专注于审计级完整记录。

6. **向后兼容 `reset_conversation()`**：重置仅清空内存中的 `_conversation_history`，不删除持久化 JSONL 文件。

## Open Questions

### Resolved During Planning

- Q: 是否需要修改 LLM Adapter 接口？A: 否，V1 使用字符数/4 估算 token，V2 可扩展为精确统计。
- Q: JSONL vs JSON 文件？A: 采用 JSONL Append-only，参照 Claude Code 设计，更适合高频写入场景。
- Q: ChromaDB 是否还需要？A: V1 简化设计，暂不引入 ChromaDB，仅使用 JSONL 持久化。向量检索在 V2 集成 EpisodicMemory。

### Deferred to Implementation

- LLM Adapter 返回精确 usage 信息（需各 Provider SDK 支持）
- 消息链 ID 和分支支持（类似 parentUuid）
- 上下文压缩摘要功能
- 向量相似会话检索

## High-Level Technical Design

```
┌─────────────────────────────────────────────────────────────────┐
│                           AIAgent                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ SessionRecordManager (新增)                                │  │
│  │  - _entries: list[Entry]  # 内存缓冲                       │  │
│  │  - _flush_timer: Timer  # 100ms 批量刷新                   │  │
│  │  - append_entry()     # 添加 Entry 到缓冲                  │  │
│  │  - _drain_write_queue()  # 批量写入 JSONL                 │  │
│  │  - shutdown()         # 确保缓冲清空                      │  │
│  └─────────────────────────────────────────────────────────┘  │
│                              ▲                                  │
│                              │ append_entry()                   │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ run_conversation() 流程:                                    │  │
│  │   1. 生成 session_id（如需要）                              │  │
│  │   2. append_entry(Entry(type="user", timestamp, ...))     │  │
│  │   3. 调用 LLMClient.call()                               │  │
│  │   4. append_entry(Entry(type="llm", timestamp,          │  │
│  │                      input_tokens, output_tokens, ...))   │  │
│  │   5. 工具调用: append_entry(Entry(type="tool", ...))      │  │
│  │   6. 返回响应                                             │  │
│  └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    JSONL Append-only 文件                        │
│  ~/.ascend_op_agent/sessions/<session_id>.jsonl                  │
│  每行一个 Entry:                                                  │
│  {"type": "user", "timestamp": 1234567890.123, ...}            │
│  {"type": "llm", "timestamp": 1234567890.456, "input_tokens": X} │
│  {"type": "tool", "timestamp": 1234567890.789, ...}            │
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Units

- U1. **定义 Entry 数据结构**

  **Goal:** 创建支持 JSONL Append-only 的 Entry 数据类型

  **Requirements:** R1, R2, R3

  **Dependencies:** None

  **Files:**
  - Create: `src/ascend_op_agent/agent/session_record.py`

  **Approach:**
  - 定义 `EntryType` 枚举：user, llm, tool
  - 定义 `Entry` dataclass：id, type, timestamp, data
  - 定义 `UserEntry`, `LLMEntry`, `ToolEntry` 子类：
    - `UserEntry`: user_input 文本
    - `LLMEntry`: system_prompt, messages, output, input_tokens, output_tokens
    - `ToolEntry`: name, arguments, result, duration_ms
  - 提供 `to_json_line()` 和 `from_json_line()` 方法
  - 提供 `to_visualization_format()` 方法

  **Patterns to follow:**
  - 参考 Claude Code `src/utils/sessionStorage.ts` 的 Entry 设计
  - 参考 `memory/episodic_memory.py` 的 dataclass 模式

  **Test scenarios:**
  - Happy path: 创建各类型 Entry 并序列化为 JSONL 格式
  - Happy path: 验证 timestamp、id 字段正确
  - Edge case: 空 messages 列表
  - Edge case: 解析畸形 JSONL 行时不崩溃

  **Verification:**
  - Entry 可正确序列化为 JSONL 行
  - JSONL 行可正确反序列化为 Entry
  - 可视化格式包含时间线数据

- U2. **创建 SessionRecordManager（JSONL Append-only）**

  **Goal:** 管理 Append-only JSONL 写入，支持批量缓冲

  **Requirements:** R1, R2, R4, R5, R6

  **Dependencies:** U1

  **Files:**
  - Create: `src/ascend_op_agent/agent/session_manager.py`
  - Modify: `src/ascend_op_agent/agent/core.py` (导入和使用 SessionRecordManager)
  - Modify: `src/ascend_op_agent/config.py` (添加 session 持久化配置)

  **Approach:**
  - SessionRecordManager 管理 Append-only JSONL 文件写入
  - `FLUSH_INTERVAL_MS = 100` 批量聚合
  - `MAX_CHUNK_BYTES = 100 * 1024 * 1024` 大文件分块
  - `append_entry()` 添加 Entry 到内存缓冲
  - `_drain_write_queue()` 批量写入文件
  - `shutdown()` 确保缓冲清空并关闭文件
  - 会话目录: `~/.ascend_op_agent/sessions/`
  - 每个 session 一个 JSONL 文件: `<session_id>.jsonl`

  **Patterns to follow:**
  - 参考 Claude Code `src/utils/sessionStorage.ts` 的 `Project` 类
  - 使用 `aiofiles` 或线程池实现异步写入（可选，V1 同步）

  **Test scenarios:**
  - Happy path: 多次 `append_entry()` 后验证 JSONL 文件行数
  - Happy path: `shutdown()` 后验证所有缓冲已写入
  - Edge case: 多轮对话的 Entry 交错写入
  - Error path: 写入失败时抛出异常而非静默丢失

  **Verification:**
  - JSONL 文件每行是一个独立的有效 JSON 对象
  - 批量写入验证：10 次 append 后文件应有 10 行（或按 flush 策略）
  - `shutdown()` 后内存缓冲为空

- U3. **集成 SessionRecordManager 到 AIAgent**

  **Goal:** 在 AIAgent.run_conversation 中嵌入记录逻辑

  **Requirements:** R1, R2, R3

  **Dependencies:** U2

  **Files:**
  - Modify: `src/ascend_op_agent/agent/core.py`

  **Approach:**
  - AIAgent 初始化时创建 SessionRecordManager 实例
  - `run_conversation()` 开始时生成或使用 session_id
  - 每条用户输入、LLM 调用、工具调用都通过 `append_entry()` 记录
  - 对话结束时（返回前）确保缓冲写入
  - `reset_conversation()` 不影响 JSONL 文件，仅清空内存 history

  **Technical design:**
  ```python
  # core.py 修改
  def run_conversation(self, user_input: str) -> str:
      session_id = self._get_or_create_session_id()
      self._session_manager.append_entry(UserEntry(
          id=str(uuid.uuid4()),
          timestamp=time.time(),
          session_id=session_id,
          user_input=user_input,
      ))

      # ... 现有逻辑 (LLM 调用) ...

      # 记录 LLM 输出
      self._session_manager.append_entry(LLMEntry(
          id=str(uuid.uuid4()),
          timestamp=time.time(),
          session_id=session_id,
          system_prompt=system_prompt,
          messages=self._conversation_history.copy(),
          output=response,
          input_tokens=len(system_prompt) // 4,  # 估算
          output_tokens=len(response) // 4,       # 估算
      ))

      # 工具调用记录
      if self._is_tool_call(response):
          # ... 执行逻辑 ...
          self._session_manager.append_entry(ToolEntry(
              id=str(uuid.uuid4()),
              timestamp=time.time(),
              session_id=session_id,
              name=tool_name,
              arguments=args,
              result=tool_result,
              duration_ms=duration_ms,
          ))
  ```

  **Patterns to follow:**
  - 最小化对现有 `run_conversation` 逻辑的侵入

  **Test scenarios:**
  - Happy path: 完整对话流程（无工具调用）生成 2 条 Entry
  - Happy path: 包含工具调用的对话生成正确数量 Entry
  - Edge case: 多轮工具调用的 Entry 顺序正确

  **Verification:**
  - JSONL 文件包含所有 Entry
  - 现有功能不受影响

- U4. **添加 session.get_history RPC 方法**

  **Goal:** 暴露会话查询接口给前端

  **Requirements:** R7, R8

  **Dependencies:** U2, U3

  **Files:**
  - Modify: `src/ascend_op_agent/backend.py`

  **Approach:**
  - 添加 `_handle_session_get_history()` 处理方法
  - 注册 `session.get_history` RPC 方法
  - 读取 JSONL 文件并返回 Entry 列表
  - 支持按 session_id 查询或获取最近会话

  **Patterns to follow:**
  - 参考现有 `session.reset` 的注册方式

  **Test scenarios:**
  - Happy path: 获取当前会话完整历史
  - Edge case: 无会话记录时返回空列表
  - Edge case: 指定不存在的 session_id 返回空

  **Verification:**
  - RPC 客户端可调用 `session.get_history` 并获得完整 Entry 列表

- U5. **添加配置项**

  **Goal:** 支持配置会话持久化路径和行为

  **Requirements:** R6

  **Dependencies:** U2

  **Files:**
  - Modify: `src/ascend_op_agent/config.py`

  **Approach:**
  - 添加 `session.persist_dir` 配置项
  - 添加 `session.max_history` 配置项
  - 添加 `session.flush_interval_ms` 配置项

  **Patterns to follow:**
  - 参考 `vector_store.persist_dir` 的配置方式

  **Test scenarios:**
  - Happy path: 使用默认配置
  - Edge case: 自定义持久化路径

  **Verification:**
  - 配置项可正确读取

## System-Wide Impact

- **Interaction graph:** AIAgent 依赖 SessionRecordManager，但不修改其他 Agent 组件接口。AIAgent 需在 `__init__` 中接收 `session_manager` 参数。
- **Error propagation:** JSONL 写入失败应抛出异常（不静默丢失），由调用方决定如何处理
- **State lifecycle risks:**
  - `shutdown()` 必须调用确保缓冲清空
  - `reset_conversation()` 不删除 JSONL 文件
  - 会话重置后，原有记录仍可通过 `session.get_history(session_id)` 查询

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 同步写入阻塞主流程 | V1 接受；Future 可引入线程池异步写入 |
| 进程崩溃导致缓冲数据丢失 | `shutdown()` 确保清理；V1 可选同步 flush |
| JSONL 文件损坏 | 每行独立 JSON，损坏行可跳过 |
| 文件过大（无分块） | MAX_CHUNK_BYTES 分块写入 |

## Documentation / Operational Notes

- 配置项: `~/.ascend_op_agent/config.yaml` 新增 `session.persist_dir`、`session.max_history`、`session.flush_interval_ms`
- Session 文件存储位置: `~/.ascend_op_agent/sessions/<session_id>.jsonl`
- JSONL 格式：每行一个 Entry，适合 `jq` 等工具解析
- 查看会话: `cat ~/.ascend_op_agent/sessions/<id>.jsonl | jq`
- V2 路线图: Token 精确统计、消息链分支、上下文压缩

## Sources & References

- **Origin document:** `docs/plans/2026-05-07-001-refactor-session-complete-conversation-record-plan.md`
- Claude Code Session Storage: `/Users/huangshilei/Documents/pythonprojects/claude-code/src/utils/sessionStorage.ts`
- Claude Code Log Types: `/Users/huangshilei/Documents/pythonprojects/claude-code/src/types/logs.ts`
- Related code: `agent/core.py`, `backend.py`
