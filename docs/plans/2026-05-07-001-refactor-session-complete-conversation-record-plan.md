---
title: "feat: Session 完整对话记录重构"
type: refactor
status: active
date: 2026-05-07
deepened: 2026-05-07
---

# Session 完整对话记录重构

## Summary

将当前仅存储 `user/assistant` 消息对的 `_conversation_history` 重构为完整的会话记录系统，完整捕获用户输入、LLM 原始输入（system prompt + conversation history）、LLM 原始输出、工具调用请求/响应，并通过 RPC 方法暴露会话查询能力。

## Problem Frame

当前 `AIAgent._conversation_history` 仅存储 `{"role": "user", "content"}` 和 `{"role": "assistant", "content"}` 两种消息，缺失：
- 发送给 LLM 的完整 system prompt
- LLM API 的原始输入/输出
- 工具调用的请求参数和执行结果
- 会话元数据（session_id、创建时间、迭代次数等）

这导致无法：
1. 调试 LLM 行为时查看完整的上下文
2. 分析工具调用失败原因
3. 在会话重启后恢复完整对话历史
4. 审计 Agent 的决策过程

## Requirements

### Data Model
- R1. Session 数据结构包含：session_id、用户输入、LLM 输入（system_prompt + history）、LLM 输出（文本响应）、工具调用列表

### Persistence & Recovery
- R2. 会话记录持久化到 ChromaDB + JSON 文件备份，支持会话重启后恢复

### Query Interface
- R3. 提供 RPC 方法 `session.get_history` 查询完整会话记录

### Backward Compatibility
- R4. 现有 `reset_conversation()` 行为保持不变（仅重置内存状态）

## Scope Boundaries

- 不修改 `MemoryStore` 的现有行为（pool-based memory）
- 不修改 `EpisodicMemory` 的向量检索功能（保持独立）
- CLI/TUI 界面保持不变

### Deferred to Follow-Up Work

- 向量相似会话检索功能（后续集成 `EpisodicMemory.search_similar_episodes`）

## Context & Research

### Relevant Code and Patterns

- `agent/core.py:67` — `_conversation_history` 仅存储 user/assistant 消息对
- `agent/core.py:96-99` — LLM 调用入口，`call()` 方法接收 system_prompt 和 history，但未记录
- `agent/core.py:154` — 工具执行 `tool.execute(**args)` 位置，尚未记录
- `memory/episodic_memory.py` — 已有的 `Episode` 数据结构，但设计用于向量检索而非完整审计记录
- `backend.py:157-158` — RPC 方法注册，`session.reset` 已存在但无 `session.get_history`
- `agent/providers/` — LLM adapter 的 `complete()` 方法仅返回文本响应（`str`），无 API 原始响应元数据

### Institutional Learnings

- 参考 Hermes Agent 的 MemoryStore 设计（`agent/memory.py:17`）
- 7 层 Prompt 设计在 `PromptBuilder` 中实现（`agent/prompt_builder.py`）

## Key Technical Decisions

1. **新建 `SessionRecordManager` 而非复用 `EpisodicMemory`**：`EpisodicMemory` 设计用于向量相似性检索，其 `Episode.turns` 不可扩展存储工具调用的结构化元数据（name/args/result），且 `_result_to_episode()` 会丢失完整 turns 记录。SessionRecordManager 专注于审计级完整记录，而非语义检索。
2. **持久化格式使用 JSON 文件 + ChromaDB 双写**：JSON 提供人类可读的完整记录备份和跨进程恢复能力；ChromaDB 提供向量检索基础（V1 仅写入，查询能力在 Deferred 中）。两者各司其职。
3. **同步写入 ChromaDB**：当前 `VectorStore` 使用同步 ChromaDB API，异步写入需要引入线程池或 AsyncClient，超出 V1 范围。V1 使用同步写入 + 错误降级。
4. **向后兼容 `reset_conversation()`**：重置仅清空内存中的 `_conversation_history`，不删除持久化记录
5. **RPC 响应格式**：返回完整的消息链，包含 role 区分（user/assistant/system/tool）

## Open Questions

### Resolved During Planning

- Q: 是否需要修改 LLM Adapter 接口？A: 否，仅在 AIAgent 层添加记录逻辑。`complete()` 返回文本，但 system_prompt + history + response 的组合已足够记录完整上下文。
- Q: ChromaDB 持久化是同步还是异步？A: V1 使用同步写入。异步写入需引入线程池，超出范围。
- Q: 历史记录是否需要压缩？A: 暂不实现，V1 仅做完整记录

### Deferred to Implementation

- 工具调用结果的存储格式（当前是字符串，未来可能需要结构化）
- Embedding 模型选择对向量检索质量的影响

## High-Level Technical Design

```
┌─────────────────────────────────────────────────────────────┐
│                        AIAgent                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ SessionRecordManager (新增)                          │   │
│  │  - _records: list[ConversationRecord]                │   │
│  │  - _current_record: ConversationRecord              │   │
│  │  - finalize_record()  # 同步写入 JSON + ChromaDB    │   │
│  │  - shutdown()  # 确保清空缓冲                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                              ▲                               │
│                              │ record()                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ run_conversation() 流程:                              │   │
│  │   1. 创建 ConversationRecord(session_id, user_input) │   │
│  │   2. 调用 LLMClient.call()                           │   │
│  │      - 记录 llm_input (system_prompt + history)      │   │
│  │      - 记录 llm_output (文本响应)                    │   │
│  │   3. 解析工具调用                                     │   │
│  │      - 记录 tool_calls[] (name, args, result)        │   │
│  │   4. 返回最终响应                                     │   │
│  │   5. 保存 record 到磁盘                               │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    VectorStore / ChromaDB                    │
│  - Collection: "sessions"                                    │
│  - 存储结构化 JSON 文件到 ~/.ascend_op_agent/sessions/       │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Units

- U1. **定义 ConversationRecord 数据结构**

  **Goal:** 创建包含完整对话信息的数据类

  **Requirements:** R1

  **Dependencies:** None

  **Files:**
  - Create: `src/ascend_op_agent/agent/session_record.py`

  **Approach:**
  - 定义 `ToolCallRecord` 存储工具调用（name, arguments, result）
  - 定义 `LLMRecord` 存储 LLM 输入输出（system_prompt, messages, raw_output）
  - 定义 `ConversationRecord` 存储完整会话记录

  **Patterns to follow:**
  - 参考 `memory/episodic_memory.py` 的 `ConversationTurn` 和 `Episode` dataclass 设计

  **Test scenarios:**
  - Happy path: 创建完整的 ConversationRecord 并序列化/反序列化
  - Edge case: 空消息列表、空工具调用列表

  **Verification:**
  - ConversationRecord 可正确 JSON 序列化

- U2. **创建 SessionRecordManager**

  **Goal:** 管理会话记录的创建、存储和查询

  **Requirements:** R1, R2

  **Dependencies:** U1

  **Files:**
  - Create: `src/ascend_op_agent/agent/session_manager.py`
  - Modify: `src/ascend_op_agent/agent/core.py` (导入和使用 SessionRecordManager)
  - Modify: `src/ascend_op_agent/config.py` (添加 session 持久化配置)

  **Approach:**
  - SessionRecordManager 管理当前会话记录和历史记录
  - 提供 `start_record(user_input)` 开启新记录
  - 提供 `record_llm_call()` 记录 LLM 输入输出
  - 提供 `record_tool_call()` 记录工具调用
  - 提供 `finalize_record()` 同步保存记录
  - 提供 `shutdown()` 确保缓冲清空
  - 同步保存到 JSON 文件 + ChromaDB（错误时降级到仅 JSON）

  **Patterns to follow:**
  - 参考 `agent/core.py` 的 `_conversation_history` 管理模式
  - 参考 `memory/vector_store.py` 的 ChromaDB 使用方式

  **Test scenarios:**
  - Happy path: 完整对话流程记录并保存
  - Edge case: 多轮工具调用场景
  - Error path: ChromaDB 写入失败时降级到仅 JSON

  **Verification:**
  - 保存的 JSON 文件包含完整记录
  - ChromaDB collection "sessions" 中可查询到会话记录（V1 仅验证写入，查询能力 Deferred）

- U3. **集成 SessionRecordManager 到 AIAgent**

  **Goal:** 在 AIAgent.run_conversation 中嵌入记录逻辑

  **Requirements:** R1

  **Dependencies:** U2

  **Files:**
  - Modify: `src/ascend_op_agent/agent/core.py`

  **Approach:**
  - AIAgent 初始化时创建 SessionRecordManager 实例
  - `run_conversation()` 开始时调用 `start_record()`
  - LLM 调用前后记录输入输出
  - 工具执行后调用 `record_tool_call()`
  - 对话结束时调用 `finalize_record()`
  - `reset_conversation()` 不再直接清空 history，而是保留记录

  **Technical design:**
  ```python
  # core.py 修改
  def run_conversation(self, user_input: str) -> str:
      self._session_manager.start_record(user_input)

      self._conversation_history.append({"role": "user", "content": user_input})
      # ... 现有逻辑 ...

      # 记录 LLM 调用
      self._session_manager.record_llm_call(
          system_prompt=system_prompt,
          messages=self._conversation_history.copy(),
          raw_output=response,
      )

      # ... 工具调用处理 ...
      if self._is_tool_call(response):
          # ... 执行逻辑 ...
          self._session_manager.record_tool_call(
              name=tool_name,
              arguments=args,
              result=tool_result,
          )
  ```

  **Patterns to follow:**
  - 最小化对现有 `run_conversation` 逻辑的侵入

  **Test scenarios:**
  - Happy path: 完整对话流程（无工具调用）
  - Happy path: 包含工具调用的对话
  - Edge case: 多轮工具调用

  **Verification:**
  - SessionRecordManager 记录完整
  - 现有功能不受影响

- U4. **添加 session.get_history RPC 方法**

  **Goal:** 暴露会话查询接口给前端

  **Requirements:** R3

  **Dependencies:** U2, U3

  **Files:**
  - Modify: `src/ascend_op_agent/backend.py`

  **Approach:**
  - 添加 `_handle_session_get_history()` 处理方法
  - 注册 `session.get_history` RPC 方法
  - 支持按 session_id 查询或获取最近 N 条会话

  **Patterns to follow:**
  - 参考现有 `session.reset` 的注册方式

  **Test scenarios:**
  - Happy path: 获取当前会话完整历史
  - Edge case: 无会话记录时返回空列表

  **Verification:**
  - RPC 客户端可调用 `session.get_history` 并获得完整记录

- U5. **添加 SessionRecordManager 的配置项**

  **Goal:** 支持配置会话持久化路径和行为

  **Requirements:** R2

  **Dependencies:** U2

  **Files:**
  - Modify: `src/ascend_op_agent/config.py`

  **Approach:**
  - 在 Config 中添加 `session.persist_dir` 配置项
  - 添加 `session.max_history` 配置项（最大保存会话数）

  **Patterns to follow:**
  - 参考 `vector_store.persist_dir` 的配置方式

  **Test scenarios:**
  - Happy path: 使用默认配置
  - Edge case: 自定义持久化路径

  **Verification:**
  - 配置项可正确读取

## System-Wide Impact

- **Interaction graph:** AIAgent 依赖 SessionRecordManager，但不修改其他 Agent 组件接口。AIAgent 需在 `__init__` 中接收 `session_manager` 参数。
- **Error propagation:** ChromaDB 写入失败仅记录 warning，降级到仅 JSON 持久化，不影响主流程
- **State lifecycle risks:**
  - SessionRecordManager 随 AIAgent 销毁时，需调用 `shutdown()` 确保缓冲清空
  - `reset_conversation()` 不删除持久化记录，仅清空内存 history
  - 会话重置后，原有记录仍可通过 `session.get_history(session_id)` 查询

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 同步 ChromaDB 写入可能阻塞主流程 | V1 接受此限制；Future 可引入线程池 |
| 进程崩溃导致缓冲数据丢失 | 关键操作后同步写入；`shutdown()` 确保清理 |
| ChromaDB 版本兼容性 | 使用 PersistentClient 而非 Client |
| JSON/ChromaDB 双写一致性问题 | ChromaDB 失败时降级到仅 JSON；定期可手动同步 |
| 记录膨胀导致内存不足 | 添加 max_history 配置；V1 仅存内存，定期写入磁盘 |

## Documentation / Operational Notes

- 配置项: `~/.ascend_op_agent/config.yaml` 新增 `session.persist_dir` 和 `session.max_history`
- Session 文件存储位置: `~/.ascend_op_agent/sessions/`
- ChromaDB collection: `sessions`
