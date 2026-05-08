---
title: 修复并完善工具调用功能
type: fix
status: active
date: 2026-05-08
deepened: 2026-05-08
---

# 修复并完善工具调用功能

## Summary

修复 `ToolRegistry` 缺失 `call_tool()` 方法的问题，完善工具调用架构，使 AIAgent 和 ACP Adapter 能够正确注册、调用和管理工具。

---

## Problem Frame

当前工具调用系统存在以下问题：

1. **致命Bug**: `ACPAdapter._handle_tools_call()` 调用 `self._tool_registry.call_tool()`，但 `ToolRegistry` 类没有此方法，导致工具调用必定失败
2. **工具调用格式依赖LLM生成XML**: 没有显式的工具调用指令集发送给LLM，LLM需要自己决定何时输出XML格式的工具调用

---

## Requirements

- R1. `ToolRegistry` 必须有 `call_tool(name, args)` 方法供 ACP Adapter 调用
- R2. 工具调用失败时应正确传播错误信息
- R3. AIAgent 应能通过 `tools` 参数向 LLM 传递可用工具列表
- R4. 内置工具应保持现有命名（`file_read`/`file_write`/`shell_exec`）

---

## Scope Boundaries

- 不涉及 LLM Provider 的修改（底层 Provider API 调用由 U4 统一扩展）
- 不涉及 Session Record 系统的修改
- 不涉及 Frontend/TUI 的修改

---

## Key Technical Decisions

- **决策**: `call_tool()` 方法返回结构化字典而非字符串
  - 原因: ACP 协议需要区分成功/失败状态，字符串无法承载此信息
  - 格式: `{"success": true, "result": <value>}` 或 `{"success": false, "error": <message>}`

- **决策**: 工具参数使用 Python 函数签名推断
  - 原因: 与现有 `@tool` 装饰器风格一致

- **决策**: U3 需要先完成 U4（适配器接口扩展）才能实现
  - 原因: 当前 `LLMClient.call()` 和 `BaseLLMAdapter.complete()` 都没有 `tools` 参数

---

## Implementation Units

- U1. **[修复 ToolRegistry.call_tool 方法]**

**Goal:** 添加缺失的 `call_tool()` 方法

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/tool_registry.py`

**Approach:**
在 `ToolRegistry` 类中添加 `call_tool(name, args)` 方法：
- 通过 `get_tool(name)` 获取工具
- 调用 `tool.execute(**args)` 并捕获异常
- 返回结构化字典: `{"success": true, "result": <value>}` 或 `{"success": false, "error": <message>}`

**Patterns to follow:**
- 参考 `Tool.execute()` 的参数解包方式 (`**kwargs`)
- 参考 `ACPAdapter._handle_tools_call()` 中 `tool_args = params.get('args', {})` 的参数获取方式

**Test scenarios:**
- Happy path: `call_tool("file_read", {"path": "/tmp/test"})` 返回 `{"success": true, "result": "..."}`
- Edge case: 调用不存在的工具返回 `{"success": false, "error": "错误: 未知工具: xxx"}`
- Error path: 工具执行抛出异常时返回 `{"success": false, "error": "错误: 工具执行失败: ..."}`

**Verification:**
- `ToolRegistry` 实例能通过 `call_tool()` 方法成功调用内置工具并返回结构化结果

---

- U2. **[确认工具命名规范]**

**Goal:** 明确内置工具命名，保持现有名称不变

**Requirements:** R4

**Dependencies:** None

**Files:**
- None (文档更新)

**Approach:**
确认现有工具名称 `file_read`/`file_write`/`shell_exec` 为最终命名，不进行重命名。原 Problem Frame 中提及的 `file_ops`/`shell_ops` 等名称期望属于超出当前范围的需求，不在本计划内处理。

**Verification:**
- 工具列表返回的名称与注册名称一致：`["file_read", "file_write", "shell_exec"]`

---

- U3. **[AIAgent 集成工具调用指令]**

**Goal:** 使 AIAgent 在调用 LLM 时传递可用工具列表

**Requirements:** R3

**Dependencies:** U4（必须先完成适配器接口扩展）

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`
- Modify: `src/ascend_op_agent/agent/providers/base.py`

**Approach:**
在 U4 完成适配器接口扩展后，通过 `tool_registry.to_openai_format()` 获取工具列表，传递给 LLM。参考 OpenAI SDK 的 tool calling 格式。

**Patterns to follow:**
- 参考 OpenAI SDK 的 tool calling 格式
- 参考 `tool_registry.to_openai_format()` 返回的格式

**Test scenarios:**
- Happy path: LLM 调用时收到正确的工具列表
- Edge case: 无工具注册时正常运作
- Error path: LLM 不支持 tools 参数时的降级处理

**Verification:**
- 带工具的 LLM 调用返回正确的 tool_call XML 格式响应

---

- U4. **[扩展 LLM 适配器接口支持 tools 参数]**

**Goal:** 为 `BaseLLMAdapter` 和 `LLMClient` 添加 `tools` 参数支持

**Requirements:** R3

**Dependencies:** U1（ToolRegistry.call_tool 是调用工具的方式）

**Files:**
- Modify: `src/ascend_op_agent/agent/providers/base.py`
- Modify: `src/ascend_op_agent/agent/core.py` (LLMClient)
- Modify: `src/ascend_op_agent/agent/providers/` (各 Provider 适配器)

**Approach:**
1. 修改 `BaseLLMAdapter.complete()` 签名添加 `tools: Optional[list[dict]] = None` 参数
2. 修改 `LLMClient.call()` 签名添加 `tools: Optional[list[dict]] = None` 参数并传递给 adapter
3. 在各 Provider 适配器（OpenAIAdapter、AnthropicAdapter 等）的 payload 构建中添加 `tools` 字段

**Technical design:**
```python
# base.py
def complete(self, system_prompt: str, conversation_history: list[dict[str, str]],
            tools: Optional[list[dict]] = None) -> str:

# core.py
def call(self, system_prompt: str, conversation_history: list[dict[str, str]],
        tools: Optional[list[dict]] = None) -> str:
    return self._adapter.complete(system_prompt, conversation_history, tools=tools)

# OpenAIAdapter.complete()
payload["tools"] = tools  # 当 tools 不为 None 时
```

**Patterns to follow:**
- 参考现有 `temperature` 参数的传递方式
- 参考各 Provider SDK 的 tools 参数格式

**Test scenarios:**
- Happy path: 带 tools 参数调用时 payload 包含 tools 字段
- Edge case: tools=None 时不传递 tools 参数（向后兼容）
- Error path: 某 Provider 不支持 tools 时的降级处理

**Verification:**
- `LLMClient.call(system_prompt, history, tools=tools_list)` 正确传递工具列表给 Provider

---

## System-Wide Impact

- **ACP Adapter**: 当前直接调用 `call_tool()`，修复后应能正常工作
- **AIAgent**: 需要验证工具调用流程是否完整
- **Frontend**: 工具列表返回格式可能需要调整

---

## Open Questions

### Deferred to Implementation

- LLM Provider 是否完整支持 tools 参数：需要验证各 Provider 的 API 能力
- 工具调用结果的结构化返回格式是否需要版本化：暂定与 ACP 协议 v1.0 配合使用
- 如果某 Provider 不支持 tools 参数：考虑降级为纯 system prompt 注入方式

---

## Sources & References

- Related code: `src/ascend_op_agent/agent/tool_registry.py`
- Related code: `src/ascend_op_agent/agent/core.py`
- Related code: `src/ascend_op_agent/acp/adapter.py` (L253)
