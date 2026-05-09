---
title: 重构工具调用系统为原生Native Function Calling架构
type: refactor
status: completed
date: 2026-05-08
deepened: 2026-05-09
completed: 2026-05-09
---

# 重构工具调用系统为原生 Native Function Calling 架构

## Summary

将 ascend_op_agent 的工具调用系统从脆弱的 XML/System Prompt 方式重构为与 Hermes Agent/Claude Code 一致的 Native Function Calling 架构：通过 LLM Provider 原生 `tools` 参数传递工具定义，利用 Provider 内置的参数验证和 tool_call 解析能力，实现可靠的工具调用流程。

---

## Problem Frame

### 架构现状

当前 ascend_op_agent 的工具调用系统存在三个层次的架构缺陷：

**Layer 1 — 工具定义已就绪但未使用**
- `Tool.to_openai_format()` 已实现 OpenAI function format
- `ToolRegistry.to_openai_format()` 可生成完整的工具列表
- 但没有任何 Adapter 接收或传递这些工具定义

**Layer 2 — 工具通过 System Prompt 文本传递（脆弱）**
- `PromptBuilder._build_tool_guidance()` 硬编码工具名称和调用格式
- 硬编码列表与实际注册工具名称完全不匹配（列出 `file_ops`/`shell_ops`，实际注册 `file_read`/`file_write`/`shell_exec`）
- LLM 需要"猜测"何时调用工具，而非被明确告知

**Layer 3 — 解析依赖正则表达式（不可靠）**
- `_is_tool_call()` 用 `<tool_call>` 和 `</tool_call>` 标签检测
- `_execute_tool_call()` 用 `re.search(r'<tool_call\s+name="(\w+)">(.+?)</tool_call>')` 解析
- 任何格式偏差都会导致工具调用静默失败

### 对比分析

| 维度 | ascend_op_agent (当前) | Hermes Agent / Claude Code (目标) |
|------|----------------------|----------------------------------|
| **工具传递方式** | System Prompt 文本描述 | Native `tools` 参数 |
| **工具调用格式** | XML: `<tool_call name="...">{"arg": "value"}</tool_call>` | OpenAI/Anthropic Native function calling |
| **参数验证** | 运行时 JSON 解析，无 schema 验证 | Provider 内置 schema 验证 |
| **LLM 适配器** | 无 `tools` 参数 | 完整支持 `tools` 参数 |
| **错误处理** | 字符串结果无结构化关联 | 结构化 `tool_call_id` 关联 |

### 根本原因

核心问题是 **LLM 适配器接口缺失 `tools` 参数**：

```
# 当前 LLMClient.call() 签名
def call(self, system_prompt: str, conversation_history: list[dict[str, str]]) -> str

# 当前 BaseLLMAdapter.complete() 签名
def complete(self, system_prompt: str, conversation_history: list[dict[str, str]]) -> str
```

`tool_registry.to_openai_format()` 存在但从未被使用。

---

## Requirements

- R1. `ToolRegistry` 必须有 `call_tool(name, args)` 方法供 ACP Adapter 调用
- R2. 工具调用失败时应正确传播错误信息
- R3. LLM 调用时通过原生 `tools` 参数传递可用工具列表
- R4. 删除 `PromptBuilder._build_tool_guidance()` 中的硬编码工具描述
- R5. 利用 Provider 内置的 tool_call 解析能力（而非正则表达式）
- R6. 工具调用结果通过 `tool_call_id` / `tool_use_id` 结构化关联

---

## Scope Boundaries

- 不涉及 Frontend/TUI 的修改
- 不涉及 LLM API 认证或 Provider 切换
- **注意**: U7（扩展 ToolEntry 支持 tool_call_id 关联）涉及 `session_record.py` 的修改，超出原定范围。实际实施时可选择：
  - 方案A: 保留 U7，从 Scope Boundaries 中移除"不涉及 Session Record 系统的修改"
  - 方案B: 从 Phase 2 中移除 U7，将 tool_call_id 关联功能推迟至后续版本

---

## Key Technical Decisions

- **决策**: 切换到 Native Function Calling
  - 原因: Hermes Agent/Claude Code 均采用此架构，Provider 内置参数验证和解析，可靠性远高于正则表达式
  - 权衡: 需要修改所有 Adapter 的 `complete()` 签名，但这是必要的架构演进

- **决策**: 工具定义通过 `tool_registry.to_openai_format()` 动态生成
  - 原因: 避免硬编码，工具列表与实际注册保持一致

- **决策**: U7 扩展 `ToolEntry` 的决定视 Scope 而定
  - 原因: U7 涉及 session_record.py 修改，若不纳入 Scope 则推迟

- **决策**: ACP Adapter 的 `call_tool()` 返回结构化字典
  - 原因: ACP 协议需要区分成功/失败状态
  - 格式: `{"success": true, "result": <value>}`
       或 `{"success": false, "error": <message>}`

- **决策**: Phase 1 实现顺序为 U1, U2, U4, U3；Phase 2 实现 U5, U6, U7
  - 原因: U4 必须先于 U3 完成，因 U3 依赖 U4 提供的 tools 参数传递能力

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

### 当前架构（问题）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ascend_op_agent                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ToolRegistry                 LLMClient            BaseLLMAdapter │
│  ┌──────────────┐             ┌──────────┐          ┌───────────┐  │
│  │ to_openai_  │ ──unused──> │  call()  │ ──?───>  │ complete()│  │
│  │ format()    │             └──────────┘          └───────────┘  │
│  └──────────────┘                                                   │
│         ↑                                                           │
│  ┌──────────────┐                                                   │
│  │ ToolEntry    │  <-- 结果记录但无关联                              │
│  └──────────────┘                                                   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐     │
│  │ PromptBuilder._build_tool_guidance()                         │     │
│  │ 硬编码: file_ops, shell_ops, ssh_ops, ascend_ops, skill_ops  │     │
│  │ (与实际注册工具完全不匹配!)                                  │     │
│  └──────────────────────────────────────────────────────────────┘     │
│                              ↓                                        │
│                     System Prompt 文本                                │
│                              ↓                                        │
│                    LLM 需要"猜测"何时调用工具                        │
│                              ↓                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ _is_tool_call() / _execute_tool_call() (正则解析)              │    │
│  │ re.search(r'<tool_call\s+name="(\w+)">(.+?)</tool_call>')    │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 目标架构（Hermes Agent / Claude Code 风格）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ascend_op_agent                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ToolRegistry                 LLMClient            BaseLLMAdapter │
│  ┌──────────────┐             ┌──────────┐          ┌───────────┐  │
│  │ to_openai_  │ ──tools──> │  call()  │ ──tools──>│ complete()│  │
│  │ format()    │             └──────────┘          └───────────┘  │
│  └──────────────┘                                                   │
│         ↑                                                           │
│  ┌──────────────┐                                                   │
│  │ ToolEntry    │  <-- tool_call_id 结构化关联                       │
│  └──────────────┘                                                   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ PromptBuilder._build_tool_guidance()                         │    │
│  │ 删除硬编码工具描述，保留使用说明（无需列举工具）              │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                              ↓                                        │
│                     System Prompt + Tools Parameter                  │
│                              ↓                                        │
│                    LLM 被明确告知可用工具及 schema                   │
│                              ↓                                        │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ Provider 内置 tool_call 解析                                  │    │
│  │ (OpenAI: response.tools[0].id, Anthropic: tool_use.id)      │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 关键文件变更

| 文件 | 变更 |
|------|------|
| `src/ascend_op_agent/agent/tool_registry.py` | U1: 添加 `call_tool()` 方法 |
| `src/ascend_op_agent/acp/adapter.py` | U1: 修复 `call_tool()` 调用 |
| `src/ascend_op_agent/agent/providers/base.py` | U4: 修改 `BaseLLMAdapter.complete()` 签名 |
| `src/ascend_op_agent/agent/core.py` | U4: 修改 `LLMClient.call()` 签名 |
| `src/ascend_op_agent/agent/providers/openai_adapter.py` | U4: 添加 `tools` 到 payload |
| `src/ascend_op_agent/agent/providers/anthropic_adapter.py` | U4: 添加 `tools` 到 API 调用 |
| `src/ascend_op_agent/agent/providers/gemini_adapter.py` | U4: 添加 `tools` 到 API 调用 |
| `src/ascend_op_agent/agent/providers/openrouter_adapter.py` | U4: 添加 `tools` 到 API 调用 |
| `src/ascend_op_agent/agent/providers/azure_openai_adapter.py` | U4: 添加 `tools` 到 API 调用 |
| `src/ascend_op_agent/agent/providers/ollama_adapter.py` | U4: 添加 `tools` 到 API 调用 |
| `src/ascend_op_agent/agent/prompt_builder.py` | U5: 删除硬编码工具描述 |

---

## Implementation Units

### Phase 1: 基础修复（阻塞性 Bug）

- U1. **[修复 ToolRegistry.call_tool 方法]**
- U2. **[确认工具命名规范]**
- U4. **[扩展 LLM 适配器接口支持 tools 参数]**
- U3. **[AIAgent 集成工具调用指令]**

### Phase 2: 架构重构（Hermes Agent 风格）

- U5. **[修改 PromptBuilder 删除硬编码工具描述]**
- U6. **[利用 Provider 内置 tool_call 解析]**
- U7. **[扩展 ToolEntry 支持 tool_call_id 关联]**（注：此条目涉及 Session Record 系统修改，与 Scope Boundaries 不一致，实际实施时应移除或更新 Scope）**

---

- U1. **[修复 ToolRegistry.call_tool 方法]**

**Goal:** 添加缺失的 `call_tool()` 方法，修复 ACP Adapter 的阻塞性调用

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/tool_registry.py`

**Approach:**
在 `ToolRegistry` 类中添加 `call_tool(name, args)` 方法：
- 通过 `get_tool(name)` 获取工具
- 调用 `tool.execute(**args)` 并捕获异常
- 返回结构化字典: `{"success": true, "result": <value>}` 或 `{"success": false, "error": <message>}`

**Technical design:**
```python
def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = self.get_tool(name)
    if not tool:
        return {"success": False, "error": f"错误: 未知工具: {name}"}
    try:
        result = tool.execute(**args)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": f"错误: 工具执行失败: {e}"}
```

**Patterns to follow:**
- 参考 `Tool.execute()` 的参数解包方式 (`**kwargs`)
- 参考 `ACPAdapter._handle_tools_call()` 中 `tool_args = params.get('args', {})` 的参数获取方式

**Test scenarios:**
- Happy path: `call_tool("file_read", {"path": "/tmp/test"})` 返回 `{"success": True, "result": "..."}`
- Edge case: 调用不存在的工具返回 `{"success": False, "error": "错误: 未知工具: xxx"}`
- Error path: 工具执行抛出异常时返回 `{"success": False, "error": "错误: 工具执行失败: ..."}`

**Verification:**
- `ToolRegistry` 实例能通过 `call_tool()` 方法成功调用内置工具并返回结构化结果

---

- U2. **[确认工具命名规范]**

**Goal:** 明确内置工具命名，保持现有名称不变，消除 Problem Frame 中的矛盾描述

**Requirements:** R4

**Dependencies:** None

**Files:**
- None (文档确认)

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
在 U4 完成适配器接口扩展后，通过 `tool_registry.to_openai_format()` 获取工具列表，传递给 LLM。

**Patterns to follow:**
- 参考 `tool_registry.to_openai_format()` 返回的 OpenAI function format

**Test scenarios:**
- Happy path: LLM 调用时收到正确的工具列表
- Edge case: 无工具注册时正常运作

**Verification:**
- 带工具的 LLM 调用正确传递工具列表给 Provider（验证 payload 中包含 tools 字段）

---

- U4. **[扩展 LLM 适配器接口支持 tools 参数]**

**Goal:** 为 `BaseLLMAdapter` 和 `LLMClient` 添加 `tools` 参数支持

**Requirements:** R3, R5

**Dependencies:** U1

**Files:**
- Modify: `src/ascend_op_agent/agent/providers/base.py`
- Modify: `src/ascend_op_agent/agent/core.py` (LLMClient)
- Modify: `src/ascend_op_agent/agent/providers/openai_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/anthropic_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/gemini_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/openrouter_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/azure_openai_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/ollama_adapter.py`

**Approach:**
1. 修改 `BaseLLMAdapter.complete()` 签名添加 `tools: Optional[list[dict]] = None` 参数
2. 修改 `LLMClient.call()` 签名添加 `tools: Optional[list[dict]] = None` 参数并传递给 adapter
3. 在各 Provider 适配器的 payload/API 调用中添加 `tools` 字段

**Technical design:**
```python
# base.py
from typing import Optional
def complete(self, system_prompt: str, conversation_history: list[dict[str, str]],
            tools: Optional[list[dict]] = None) -> str:
    ...

# core.py - LLMClient.call()
def call(self, system_prompt: str, conversation_history: list[dict[str, str]],
         tools: Optional[list[dict]] = None) -> str:
    return self._adapter.complete(system_prompt, conversation_history, tools=tools)

# openai_adapter.py - payload 构建
payload["tools"] = tools  # 当 tools 不为 None 时添加到 payload

# anthropic_adapter.py - messages.create() 调用
client.messages.create(model=self.model, messages=messages, tools=tools, ...)
```

**Patterns to follow:**
- 参考现有 `temperature` 参数的传递方式
- 参考各 Provider SDK 的 tools 参数格式

**Test scenarios:**
- Happy path: 带 tools 参数调用时 payload 包含 tools 字段
- Edge case: tools=None 时不传递 tools 参数（向后兼容）
- Integration: OpenAI Provider 收到 tools 后生成 `tool_calls` 响应

**Verification:**
- `LLMClient.call(system_prompt, history, tools=tools_list)` 正确传递工具列表给 Provider

---

- U5. **[修改 PromptBuilder 删除硬编码工具描述]**

**Goal:** 删除 `PromptBuilder._build_tool_guidance()` 中的硬编码工具名称列表，改为通用的工具使用指导

**Requirements:** R4

**Dependencies:** U4

**Files:**
- Modify: `src/ascend_op_agent/agent/prompt_builder.py`

**Approach:**
删除硬编码的工具列表（`file_ops`, `shell_ops`, `ssh_ops`, `ascend_ops`, `skill_ops`），保留通用的工具使用说明，不再列举具体工具名称。工具清单通过 `tools` 参数传递给 LLM，无需在 prompt 中重复。

**Technical design:**
```python
def _build_tool_guidance(self) -> str:
    return """## Tool Usage

当需要执行操作时，你可以调用工具。工具参数将根据其 schema 进行验证。

重要:
- 工具调用后等待结果再继续
- 错误时重试或尝试替代方案
- 敏感操作需用户确认
"""
```

**Verification:**
- `PromptBuilder` 输出的 prompt 中不再包含硬编码的工具名称

---

- U6. **[利用 Provider 内置 tool_call 解析]**

**Goal:** 利用 OpenAI/Anthropic Provider 内置的 tool_call 解析能力，替代当前的正则表达式解析

**Requirements:** R5

**Dependencies:** U4

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`
- Modify: `src/ascend_op_agent/agent/providers/openai_adapter.py`
- Modify: `src/ascend_op_agent/agent/providers/anthropic_adapter.py`

**Approach:**
OpenAI 和 Anthropic 的 SDK 已经内置了 tool_call 解析：
- **OpenAI**: 响应中的 `response.tool_calls[0]` 包含 `{"id": "tool_xxx", "function": {"name": "tool_name", "arguments": "{...}"}}`
- **Anthropic**: 需要迭代 `response.content` 列表查找 `type == "tool_use"` 的 block，其包含 `{"id": "toolu_xxx", "name": "tool_name", "input": {...}}`

需要修改 Adapter 返回值以传递这些结构化信息，修改 `AIAgent._execute_tool_call()` 以使用 Provider 返回的 tool_call 而非正则解析。

**Technical design:**
```python
# adapter 返回结构化 tool_call 信息
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    raw_response: Any

# openai_adapter.py
def complete(self, ...) -> str | ToolCallResult:
    response = client.chat.completions.create(...)
    if response.tool_calls and len(response.tool_calls) > 0:
        first_tool_call = response.tool_calls[0]
        return ToolCallResult(
            tool_call_id=first_tool_call.id,
            tool_name=first_tool_call.function.name,
            arguments=json.loads(first_tool_call.function.arguments),
            raw_response=response
        )
    return response.content

# anthropic_adapter.py - 需要迭代 content blocks 查找 tool_use
def complete(self, ...) -> str | ToolCallResult:
    response = client.messages.create(...)
    for content_block in response.content:
        if content_block.type == "tool_use":
            return ToolCallResult(
                tool_call_id=content_block.tool_use.id,
                tool_name=content_block.tool_use.name,
                arguments=content_block.tool_use.input,
                raw_response=response
            )
    # 从 content 列表中找到 text 块返回
    for content_block in response.content:
        if content_block.type == "text":
            return content_block.text
    return ""

# core.py - _execute_tool_call
def _execute_tool_call(self, tool_call_info: ToolCallResult) -> str:
    tool = self.tool_registry.get_tool(tool_call_info.tool_name)
    if not tool:
        return f"错误: 未知工具: {tool_call_info.tool_name}"
    result = tool.execute(**tool_call_info.arguments)
    return str(result)
```

**Patterns to follow:**
- OpenAI SDK 的 `response.tool_calls[0].function.name` 和 `response.tool_calls[0].function.arguments` 解析
- Anthropic SDK 需要迭代 `response.content` 列表，查找 `type == "tool_use"` 的 content block

**Test scenarios:**
- Happy path: Provider 返回结构化 tool_call 时正确解析并执行
- Edge case: Provider 不返回 tool_call（纯文本响应）时正常处理
- Error path: tool_name 不存在于 registry 时返回错误

**Verification:**
- 工具调用流程完全通过 Provider 内置解析，不再使用正则表达式

---

- U7. **[扩展 ToolEntry 支持 tool_call_id 关联]**

**Goal:** 在 `ToolEntry` 中增加 `tool_call_id` 字段以支持结构化关联

**Requirements:** R6

**Dependencies:** U6

**Files:**
- Modify: `src/ascend_op_agent/agent/session_record.py`
- Modify: `src/ascend_op_agent/agent/core.py`

**Approach:**
扩展 `ToolEntry` dataclass 增加 `tool_call_id: str = ""` 字段，在记录工具调用时填充该字段以关联原生 tool_call ID。

**Technical design:**
```python
@dataclass
class ToolEntry(Entry):
    type: str = "tool"
    tool_name: str = ""
    tool_call_id: str = ""  # 新增：关联原生 tool_call ID
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: Optional[str] = None
```

**Verification:**
- `ToolEntry` 记录包含正确的 `tool_call_id`

---

## System-Wide Impact

- **ACP Adapter**: `call_tool()` 修复后应能正常工作
- **AIAgent**: 需要适配新的结构化 tool_call 返回格式
- **Frontend**: 工具列表返回格式保持不变
- **所有 Provider 适配器**: 需要统一添加 `tools` 参数支持

---

## Open Questions

### Deferred to Implementation

- Provider 不支持 `tools` 参数时的降级处理
- 结构化 `ToolCallResult` 的类型定义位置
- 是否需要处理 `tool_call` 部分成功的情况（某些 tool_call 失败）

---

## Sources & References

- Related code: `src/ascend_op_agent/agent/tool_registry.py`
- Related code: `src/ascend_op_agent/agent/core.py`
- Related code: `src/ascend_op_agent/agent/prompt_builder.py`
- Related code: `src/ascend_op_agent/acp/adapter.py` (L253)
- Hermes Agent tool calling architecture (internal research)
