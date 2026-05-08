---
title: 修复并完善工具调用功能
type: fix
status: active
date: 2026-05-08
---

# 修复并完善工具调用功能

## Summary

修复 `ToolRegistry` 缺失 `call_tool()` 方法的问题，完善工具调用架构，使 AIAgent 和 ACP Adapter 能够正确注册、调用和管理工具。

---

## Problem Frame

当前工具调用系统存在以下问题：

1. **致命Bug**: `ACPAdapter._handle_tools_call()` 调用 `self._tool_registry.call_tool()`，但 `ToolRegistry` 类没有此方法，导致工具调用必定失败
2. **命名不一致**: 内置工具名为 `file_read`/`file_write`/`shell_exec`，但用户期望 `file_ops`/`shell_ops` 等名称
3. **工具调用格式依赖LLM生成XML**: 没有显式的工具调用指令集发送给LLM，LLM需要自己决定何时输出XML格式的工具调用

---

## Requirements

- R1. `ToolRegistry` 必须有 `call_tool(name, args)` 方法供 ACP Adapter 调用
- R2. 工具调用结果应统一返回结构化格式而非字符串
- R3. AIAgent 应能通过 `tools` 参数向 LLM 传递可用工具列表
- R4. 内置工具应使用一致的命名规范
- R5. 工具调用失败时应正确传播错误信息

---

## Scope Boundaries

- 不涉及 LLM Provider 的修改
- 不涉及 Session Record 系统的修改
- 不涉及 Frontend/TUI 的修改

---

## Key Technical Decisions

- **决策**: `call_tool()` 方法返回结构化字典而非字符串
  - 原因: ACP 协议需要区分成功/失败状态，字符串无法承载此信息

- **决策**: 工具参数使用 Python 函数签名推断
  - 原因: 与现有 `@tool` 装饰器风格一致

---

## Implementation Units

- U1. **[修复 ToolRegistry.call_tool 方法]**

**Goal:** 添加缺失的 `call_tool()` 方法

**Requirements:** R1, R5

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/tool_registry.py`

**Approach:**
在 `ToolRegistry` 类中添加 `call_tool(name, args)` 方法：
- 通过 `get_tool(name)` 获取工具
- 调用 `tool.execute(**args)`
- 捕获异常并返回结构化结果

**Patterns to follow:**
- 参考 `ACPAdapter._handle_tools_call()` 的调用方式

**Test scenarios:**
- Happy path: `call_tool("file_read", {"path": "/tmp/test"})` 返回正确结果
- Edge case: 调用不存在的工具返回错误结构
- Error path: 工具执行抛出异常时返回错误结构

**Verification:**
- `ToolRegistry` 实例能通过 `call_tool()` 方法成功调用内置工具

---

- U2. **[统一工具命名规范]**

**Goal:** 将内置工具重命名为更清晰的名称

**Requirements:** R4

**Dependencies:** U1

**Files:**
- Modify: `src/ascend_op_agent/agent/tool_registry.py`

**Approach:**
重命名内置工具：
- `file_read` → `file_read` (保持)
- `file_write` → `file_write` (保持)
- `shell_exec` → `shell_exec` (保持)

如需新增 `shell_ops`/`file_ops` 分组工具，作为独立工具注册。

**Patterns to follow:**
- 保持现有 `@tool` 装饰器用法

**Test scenarios:**
- Happy path: 新旧名称都能正确调用对应工具

**Verification:**
- 工具列表返回的名称与注册名称一致

---

- U3. **[AIAgent 集成工具调用指令]**

**Goal:** 使 AIAgent 在调用 LLM 时传递可用工具列表

**Requirements:** R3

**Dependencies:** U1

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`
- Modify: `src/ascend_op_agent/agent/providers/` (检查各 Provider 是否支持 tools 参数)

**Approach:**
检查 LLM Adapter 是否支持 `tools` 参数，如支持则在 `LLMClient.call()` 时传入 `tool_registry.to_openai_format()`。

**Patterns to follow:**
- 参考 OpenAI SDK 的 tool calling 格式

**Test scenarios:**
- Happy path: LLM 调用时收到正确的工具列表
- Edge case: 无工具注册时正常运作

**Verification:**
- 带工具的 LLM 调用返回正确的 tool_call XML 格式响应

---

## System-Wide Impact

- **ACP Adapter**: 当前直接调用 `call_tool()`，修复后应能正常工作
- **AIAgent**: 需要验证工具调用流程是否完整
- **Frontend**: 工具列表返回格式可能需要调整

---

## Open Questions

### Deferred to Implementation

- LLM Provider 是否完整支持 tools 参数？需要运行时验证
- 工具调用结果的结构化返回格式是否需要版本化？

---

## Sources & References

- Related code: `src/ascend_op_agent/agent/tool_registry.py`
- Related code: `src/ascend_op_agent/agent/core.py`
- Related code: `src/ascend_op_agent/acp/adapter.py` (L253)
