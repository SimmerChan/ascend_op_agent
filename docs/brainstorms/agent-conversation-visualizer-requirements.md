# Agent 对话可视化调试器

## 概述

为 ascend_op_agent 构建 Web 可视化工具，用于调试 Agent 与用户的完整交互流程。支持时间顺序和对话层级关系的展示。

## 背景问题

当前 Session Record 系统存在以下问题：
1. **LLMEntry 未记录** - AIAgent 调用 LLM 后没有记录响应内容
2. **无层级关系** - Entry 之间无父子引用，无法构建树形结构
3. **调试困难** - 无法直观看到完整调用链：User → LLM → Tools → LLM → Response

## 用户场景

- 调试 Agent 工具调用流程
- 分析多轮对话上下文
- 排查工具调用异常
- 可视化 ACP 协议会话和 Backend CLI 会话两种模式

## 功能需求

### FR-1: 数据层改造

**FR-1.1** Entry 添加 `parent_id` 字段，支持构建父子关系链

**FR-1.2** AIAgent 补录 LLMEntry，记录：
- `input_messages`: 发送给 LLM 的完整消息列表
- `output_content`: LLM 响应文本
- `tool_calls`: 工具调用列表（包含 tool_call_id, name, arguments）

**FR-1.3** 支持以下 Entry 类型及其父子关系：
```
UserEntry (parent=null)
  └── LLMEntry (parent=user)
        ├── ToolEntry (parent=llm) × N
        └── LLMEntry (parent=llm, 当有工具调用时) × N
```

### FR-2: 可视化服务

**FR-2.1** 新建独立 viewer 服务（FastAPI + Vue3）

**FR-2.2** 读取现有 JSONL 文件，重建树结构

**FR-2.3** 提供 API：
- `GET /api/sessions` - 列出所有会话
- `GET /api/sessions/:id` - 获取指定会话的完整树结构
- `GET /api/sessions/:id/entries` - 获取扁平 Entry 列表

### FR-3: 可视化界面

**FR-3.1** 树形展示交互流程，支持展开/折叠

**FR-3.2** 每条 Entry 显示：
- 类型图标 (User/LLM/Tool)
- 时间戳
- 内容预览（可展开查看完整内容）
- Token 计数

**FR-3.3** LLM Entry 展示：
- 输入消息摘要
- 输出内容（可展开）
- 工具调用列表（如果有）

**FR-3.4** Tool Entry 展示：
- 工具名称
- 调用参数
- 执行结果/错误

### FR-4: 会话类型区分

**FR-4.1** 区分 ACP 和 CLI 两种会话源

**FR-4.2** 按会话源筛选

## 技术方案

### 数据层

- Entry dataclass 添加 `parent_id: Optional[str]`
- AIAgent.run_conversation 补录 LLMEntry，建立父子关系

### 可视化服务

```
ascend_op_agent/
└── viewer/                    # 新建可视化服务
    ├── backend/               # FastAPI 后端
    │   └── src/
    │       ├── routes/        # API 路由
    │       ├── services/      # 树构建服务
    │       └── main.py
    └── frontend/              # Vue3 前端
        └── src/
            ├── views/         # 页面
            └── components/    # 组件
```

### 复用 claude-session-dashboard

参考其前端设计：
- Vue3 + Element Plus
- Pinia 状态管理
- 树形组件渲染

## 成功标准

1. 能读取现有 JSONL 数据（向后兼容）
2. 能正确构建并展示树形层级关系
3. 能展示完整 LLM 输入输出
4. 能展示工具调用及结果
5. 界面支持展开/折叠操作

## 范围边界

### 纳入
- 数据层改造（Entry + AIAgent）
- 独立 viewer 服务
- JSONL 兼容读取

### 不纳入
- 修改现有 SessionRecordManager 写入逻辑
- 修改 backend.py 的 RPC 逻辑
- 修改 CLI 的 TUI 逻辑

## 依赖假设

1. JSONL 文件格式保持向后兼容
2. Entry 类型字段保持稳定
3. 可以复用 claude-session-dashboard 的前端组件模式

## 实施步骤

1. **Phase 1**: 数据层改造
   - Entry 添加 parent_id
   - AIAgent 补录 LLMEntry

2. **Phase 2**: Viewer 后端
   - 读取 JSONL，重建树结构 API

3. **Phase 3**: Viewer 前端
   - 树形展示界面
   - 复用 dashboard 设计模式

---

*创建时间: 2026-05-10*
*状态: 已确认方案，待执行*
