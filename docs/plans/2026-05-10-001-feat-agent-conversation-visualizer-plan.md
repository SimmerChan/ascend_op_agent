---
title: Agent 对话可视化调试器
type: feat
status: active
date: 2026-05-10
origin: docs/brainstorms/agent-conversation-visualizer-requirements.md
---

# Agent 对话可视化调试器

## Summary

构建独立的 Web 可视化服务，读取 ascend_op_agent 现有 JSONL 会话记录，后端构建树形结构返回前端，前端以可展开树形方式展示 Agent 与用户的完整交互流程：User → LLM → Tools → LLM → Response。

---

## Problem Frame

当前 Session Record 系统仅记录 UserEntry、SystemEntry、ToolEntry，缺少 LLMEntry（LLM 响应未记录），且 Entry 之间无父子关系（parent_id），无法构建树形层级。可视化调试工具需要完整展示工具调用链和嵌套关系，帮助排查 Agent 行为异常。

---

## Requirements

- R1. Entry 添加 `parent_id: Optional[str]` 字段，支持父子关系链
- R2. AIAgent 补录 LLMEntry，记录完整 LLM 输入（input_messages）和输出（output_content）
- R3. Viewer 后端读取现有 JSONL 文件，构建嵌套树结构 API
- R4. Viewer 前端树形展示交互流程，支持展开/折叠
- R5. LLM Entry 展示输入消息摘要、输出内容、工具调用列表
- R6. Tool Entry 展示工具名称、调用参数、执行结果/错误
- R7. 区分 ACP 和 CLI 两种会话源，支持按会话源筛选

---

## Scope Boundaries

### 纳入
- 数据层改造（Entry + parent_id，AIAgent 补录 LLMEntry）
- 独立 viewer 服务（FastAPI + Vue3）
- JSONL 兼容读取（复用现有 session_manager）

### 不纳入
- 修改现有 SessionRecordManager 写入逻辑
- 修改 backend.py 的 RPC 逻辑
- 修改 CLI 的 TUI 逻辑

---

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/agent/session_record.py` - Entry dataclass 定义，需添加 parent_id
- `src/ascend_op_agent/agent/core.py` - AIAgent.run_conversation，需补录 LLMEntry
- `src/ascend_op_agent/agent/session_manager.py` - read_session_history() 读取 JSONL
- `claude_session_dashboard/packages/frontend/` - Vue3 + Element Plus 前端模式参考

### Institutional Learnings

- SessionRecordManager 采用 JSONL Append-only 格式，ThreadPoolExecutor + Queue 异步写入
- Entry 类型：UserEntry、SystemEntry、LLMEntry（未使用）、ToolEntry
- turn_id 用于标识迭代轮次，可用于父子关系排序

---

## Key Technical Decisions

- **后端框架**: FastAPI (Python)，与 ascend_op_agent 同一 runtime，可直接复用 Entry 类型和 session_manager
- **树构建**: 后端构建，API 返回嵌套结构，前端直接渲染无需递归构建
- **类型共享**: Python dataclass → Pydantic → OpenAPI schema，前端通过自动生成 TypeScript 类型
- **会话源区分**: 通过 session_id 前缀或 metadata 字段区分 ACP/CLI 会话

---

## Open Questions

### Resolved During Planning

- Q: Entry 的 parent_id 如何建立？A: 在 AIAgent.run_conversation 中，UserEntry.id → LLMEntry.parent_id，LLMEntry.id → ToolEntry.parent_id
- Q: 树节点 ID 是否稳定？A: 使用 Entry.id 作为树节点唯一标识，parent_id 引用父节点 ID

### Deferred to Implementation

- 工具调用结果的 content 类型是否需要区分 tool_use/tool_result？待查看现有工具实现
- 前端树形组件使用 Element Plus el-tree 或自递归组件？待前端实现时决定

---

## Output Structure

```
ascend_op_agent/
└── viewer/
    ├── backend/
    │   ├── src/
    │   │   ├── main.py              # FastAPI 入口
    │   │   ├── routes/
    │   │   │   └── sessions.py      # 会话 API 路由
    │   │   ├── services/
    │   │   │   └── tree_builder.py # 树构建服务
    │   │   └── schemas/
    │   │       └── session.py       # Pydantic 类型定义
    │   └── pyproject.toml
    └── frontend/
        ├── src/
        │   ├── views/
        │   │   └── SessionTreeView.vue
        │   ├── components/
        │   │   └── TreeNode.vue
        │   └── api/
        │       └── sessions.ts
        └── package.json
```

---

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

### 数据流

```
AIAgent.run_conversation()
  ├─ UserEntry (id=U1, parent_id=null)
  ├─ SystemEntry (id=S1, parent_id=U1)
  ├─ LLMEntry (id=L1, parent_id=U1, input=[...], output="...")
  │   ├─ ToolEntry (id=T1, parent_id=L1, tool_name="xxx", arguments={...})
  │   └─ LLMEntry (id=L2, parent_id=L1, ...)  ← 工具调用后继续迭代
  └─ LLMEntry (id=L3, parent_id=U1, output="final response")  ← 最终回复
```

### 后端树构建逻辑

```
read_session_history(session_id)
  ↓ 读取扁平 Entry 列表
build_tree(entries)
  ↓ 按 parent_id 构建父子映射
  ↓ 根节点: parent_id == null
  ↓ 返回嵌套 TreeNode { id, entry, children: [...] }
```

### API 设计

```python
# GET /api/sessions
# 返回: { sessions: [{session_id, source, created_at, entry_count}] }

# GET /api/sessions/{session_id}/tree
# 返回: { tree: TreeNode }  # 嵌套结构

# GET /api/sessions/{session_id}/entries
# 返回: { entries: [扁平 Entry 列表] }
```

---

## Implementation Units

- U1. **[Entry 数据结构改造]**

**Goal:** 为 Entry 添加 parent_id 字段，使能构建父子关系链

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/session_record.py`

**Approach:**
- Entry 基类添加 `parent_id: Optional[str] = None` 字段
- 各子类（UserEntry、SystemEntry、LLMEntry、ToolEntry）继承此字段
- entry_from_dict / entry_from_json 支持 parent_id 反序列化

**Patterns to follow:**
- 现有 Entry dataclass 结构

**Test scenarios:**
- Happy path: 反序列化包含 parent_id 的 JSON，Entry.parent_id 正确赋值
- Edge case: parent_id 为 null 或空字符串时的处理
- Backward compatibility: 不包含 parent_id 的旧 JSONL 仍能正确解析

**Verification:**
- 现有 JSONL 文件可被正确读取，parent_id 字段默认为 None

---

- U2. **[AIAgent 补录 LLMEntry]**

**Goal:** AIAgent 调用 LLM 后记录 LLMEntry，建立父子关系链

**Requirements:** R2

**Dependencies:** U1

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`
- Test: `tests/unit/agent/test_core.py`（新建）

**Approach:**
- LLMClient.call() 返回值包装为 LLMEntry
- 记录 input_messages（system_prompt + conversation_history）、output_content
- 建立父子关系：UserEntry → LLMEntry，LLMEntry → ToolEntry，ToolEntry → 后续 LLMEntry
- 当 LLM 返回工具调用时，ToolEntry.parent_id = LLMEntry.id
- 工具调用后的下一个 LLMEntry.parent_id = ToolEntry.id（如果继续迭代）

**Patterns to follow:**
- 现有 _safe_append / _execute_tool_call 模式

**Test scenarios:**
- Happy path: 单轮对话正确记录 LLMEntry 和父子关系
- Happy path: 工具调用场景正确记录 ToolEntry 和后续 LLMEntry
- Error path: LLM 调用失败时仍记录失败的 ToolEntry

**Verification:**
- 日志或调试输出确认 LLMEntry 被正确创建和记录

---

- U3. **[Viewer 后端 - 树构建服务]**

**Goal:** 读取 JSONL 文件，构建嵌套树结构 API

**Requirements:** R3

**Dependencies:** U2（需要 LLMEntry 数据）

**Files:**
- Create: `viewer/backend/src/main.py`
- Create: `viewer/backend/src/routes/sessions.py`
- Create: `viewer/backend/src/services/tree_builder.py`
- Create: `viewer/backend/src/schemas/session.py`
- Create: `viewer/backend/pyproject.toml`

**Approach:**
- 复用 ascend_op_agent 包的 session_manager 和 session_record
- FastAPI 路由读取 JSONL，调用 tree_builder 构建嵌套结构
- API 返回两种模式：扁平 entries 和嵌套 tree
- 支持按 session_id 前缀区分 ACP/CLI 会话

**Patterns to follow:**
- ascend_op_agent 现有 SessionRecordManager 读取逻辑
- FastAPI 路由模式（参考 backend.py RPC handler）
- CLI 子命令模式（参考 acp/command.ts）

**Test scenarios:**
- Happy path: 正确读取 JSONL 并构建嵌套树
- Edge case: 空会话或仅有一条 Entry 的会话
- Edge case: parent_id 断裂时的降级处理（显示为根节点）

**Verification:**
- API 返回的 JSON 结构与前端期望的 TreeNode 一致

---

### U3.5 CLI 集成

**CLI 命令设计:**
```
ascend_op_agent viewer          # 启动 viewer 服务（后端 + 前端）
ascend_op_agent viewer --only-backend  # 仅启动后端
ascend_op_agent viewer --port 3001     # 指定端口
```

**集成位置:**
- Modify: `src/ascend_op_agent/cli.py` - 添加 `viewer` 子命令
- 或 Create: `src/ascend_op_agent/cli/viewer.py` - viewer 命令模块

**启动逻辑:**
1. 检查端口是否可用
2. 启动 FastAPI 后端服务（subprocess）
3. 启动前端 dev server（subprocess，可选）
4. 打开浏览器
5. Ctrl+C 关闭所有进程

---

- U4. **[Viewer 前端 - 树形展示组件]**

**Goal:** Vue3 前端树形展示会话交互流程，支持展开/折叠

**Requirements:** R4, R5, R6, R7

**Dependencies:** U3

**Files:**
- Create: `viewer/frontend/src/views/SessionTreeView.vue`
- Create: `viewer/frontend/src/components/TreeNode.vue`
- Create: `viewer/frontend/src/api/sessions.ts`
- Create: `viewer/frontend/src/stores/session.ts`
- Create: `viewer/frontend/src/App.vue`
- Create: `viewer/frontend/src/main.ts`
- Create: `viewer/frontend/package.json`
- Create: `viewer/frontend/vite.config.ts`
- Create: `viewer/frontend/tsconfig.json`

**Approach:**
- 复用 claude-session-dashboard 前端设计模式
- 使用 Element Plus el-tree 或自递归组件展示树形
- LLM Entry 展示：输入消息数、输出内容（可展开）、工具调用数
- Tool Entry 展示：工具名称、参数 JSON（可展开）、结果/错误
- 顶部筛选器支持按会话源（ACP/CLI）筛选
- Pinia Store 管理会话状态

**Patterns to follow:**
- claude-session-dashboard: SessionDetailView.vue、SessionCard.vue
- Element Plus el-tree 组件用法
- Pinia Store 模式

**Test scenarios:**
- Happy path: 树形正确展示，父子节点缩进正确
- Happy path: 点击展开/折叠正常工作
- Happy path: LLM Entry 展开显示完整输入输出
- Happy path: Tool Entry 展开显示参数和结果
- Edge case: 长文本内容截断和展开
- Edge case: 工具调用失败显示错误样式

**Verification:**
- 页面正常渲染，API 数据正确显示，树形交互正常

---

- U5. **[前后端联调与集成测试]**

**Goal:** 验证完整数据流：JSONL → 后端 API → 前端展示

**Requirements:** R1, R2, R3, R4, R5, R6, R7

**Dependencies:** U1, U2, U3, U4

**Files:**
- Test: `tests/integration/test_viewer_e2e.py`（新建）

**Approach:**
- 启动 viewer 后端服务
- 启动 viewer 前端（开发模式）
- 使用现有会话数据验证完整流程
- 检查控制台无报错

**Patterns to follow:**
- 现有 integration test 模式

**Test scenarios:**
- Happy path: 完整会话从读取到前端展示全流程
- Edge case: 旧 JSONL 文件（无 parent_id）降级展示

**Verification:**
- 前后端服务正常启动，页面可访问，数据正确展示

---

## System-Wide Impact

- **Interaction graph:** 数据层改动（U1、U2）影响 SessionRecordManager 读取逻辑，但写入逻辑不变
- **Error propagation:** LLMEntry 记录失败不影响 Agent 主流程，使用 _safe_append 模式
- **API surface parity:** Viewer API 独立于现有 backend.py RPC，不影响现有功能
- **Integration coverage:** 需验证现有 JSONL 格式兼容（向后兼容）

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| parent_id 建立错误导致树形结构断裂 | U2 测试覆盖工具调用父子关系场景 |
| 旧 JSONL 数据无 parent_id | U3 实现降级处理，U5 测试覆盖 |
| FastAPI 与前端类型不一致 | 使用 Pydantic → OpenAPI schema → TypeScript 生成 |

---

## Documentation / Operational Notes

- viewer 服务通过 CLI 集成：`ascend_op_agent viewer`
- 启动后端：`cd viewer/backend && uvicorn main:app --reload`
- 前端开发：`cd viewer/frontend && npm run dev`
- 浏览器自动打开 `http://localhost:3001`（默认端口 3001，与主服务 3000 区分）

---

## Sources & References

- **Origin document:** [docs/brainstorms/agent-conversation-visualizer-requirements.md](../brainstorms/agent-conversation-visualizer-requirements.md)
- **前端参考:** claude_session_dashboard/packages/frontend/
- **相关代码:** `src/ascend_op_agent/agent/session_record.py`, `src/ascend_op_agent/agent/core.py`, `src/ascend_op_agent/agent/session_manager.py`
