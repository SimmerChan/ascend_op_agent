# 架构设计

## 系统架构

整个系统采用**分层架构**设计，从上到下分为六层：用户交互层、编排层、Agent 核心引擎、知识层、运行环境层、安全层。每一层各管一段职责，层间通过明确的接口解耦。

```mermaid
graph TB
    subgraph 用户交互层["用户交互层"]
        CLI["CLI 命令行<br/>ascend-op-agent run / task / viewer"]
        TUI["双进程 TUI<br/>Ink + React"]
        IDE["IDE 集成<br/>ACP: VS Code / Zed / JetBrains"]
    end

    subgraph 编排层["编排层 (自研 PhaseRunner 状态机, 非 LangGraph)"]
        ENTRY["入口路由<br/>op: 前缀 / TaskRouter"]
        PHASE["PhaseRunner<br/>顺序 DAG + 条件路由 + HITL"]
        CKPT["CheckpointStore<br/>SQLite v2 + WAL + 崩溃恢复"]
        FIX["Fix Loop<br/>review -> fix -> re-review 闭环"]
        TASK["TaskRouter / TaskStore<br/>任务类型路由 + 状态 rollup"]
    end

    subgraph Agent核心["Agent 核心引擎"]
        AGENT["AIAgent<br/>会话循环 + 工具调用"]
        PROMPT["PromptBuilder<br/>7 层 Prompt 组装"]
        MEMORY["四层记忆系统"]
        TOOLS["ToolRegistry<br/>file / git / shell / npu / skill 工具"]
    end

    subgraph 知识层["知识层"]
        CANNBOT["cannbot-skills 加载器<br/>消费华为官方 Skill 仓库"]
        SKILL["Skill 知识库<br/>混合检索 (FTS5 + 向量) + 结晶化"]
    end

    subgraph 运行环境层["运行环境层"]
        SSH["SSH 远程开发<br/>连接管理 + 文件同步"]
        NPU["NpuExecutor<br/>SSH -> docker exec ops_pt -> build.sh"]
        DOCKER["CANN 开发容器<br/>910B3 真编译 + ST 驱动验证"]
    end

    subgraph 安全层["安全层"]
        CRED["CredentialManager<br/>SSH 凭据管理"]
        TOKEN["TokenResolver<br/>${VAR} / ${VAR:-default} 解析"]
    end

    CLI --> ENTRY
    TUI --> AGENT
    IDE --> AGENT
    ENTRY --> PHASE
    PHASE --> CKPT
    PHASE --> FIX
    ENTRY --> TASK
    PHASE --> AGENT
    AGENT --> PROMPT
    AGENT --> MEMORY
    AGENT --> TOOLS
    PROMPT --> CANNBOT
    PROMPT --> SKILL
    PHASE --> NPU
    NPU --> SSH
    SSH --> DOCKER
    SSH --> CRED
    CRED --> TOKEN

    style 用户交互层 fill:#e1f5fe,stroke:#0288d1
    style 编排层 fill:#fff3e0,stroke:#f57c00
    style Agent核心 fill:#e8f5e9,stroke:#388e3c
    style 知识层 fill:#f3e5f5,stroke:#7b1fa2
    style 运行环境层 fill:#fce4ec,stroke:#c62828
    style 安全层 fill:#f5f5f5,stroke:#616161
```

### 编排层：自研 PhaseRunner

项目最关键的技术决策之一是**自研轻量状态机编排器**而非直接使用 LangGraph。编排层由以下模块组成（`src/ascend_op_agent/orchestrator/`）：

| 模块 | 职责 |
|------|------|
| `state_machine.py` | PhaseRunner 顺序 DAG 引擎：按 `nodes` 列表顺序执行、应用 reducer、每节点后持久化 checkpoint、处理 `__interrupt__`（HITL 暂停）和 `__status__`（done/failed 终止）、`resume` 从 checkpoint 续跑 |
| `state.py` | OpState（TypedDict）+ reducer 规则：`messages`/`phase_history` append，`memory_pools`/`retry_counts` dict merge，其余 last-write-wins |
| `checkpoint.py` | CheckpointStore（SQLite v2）：三表 schema（checkpoints / artifacts / pending_approvals）+ WAL + busy_timeout + v1↔v2 迁移 + quarantine LRU |
| `npu_exec.py` | NpuExecutor：SSH -> docker exec ops_pt -> `bash build.sh --soc=ascend910b` 真编译 + ST 驱动精度验证（NPU 执行 + CPU golden + MERE/MARE 比对） |
| `fix_loop.py` | run_fix_loop：review -> fix -> re-review 收敛循环 + compress_transcript token 压缩 |
| `cannbot_loader.py` | cannbot-skills 加载器：消费华为官方 skill 仓库 + SkillUsageRegistry（signal-1 跟踪）+ SKILL_BUNDLES 决策表 |
| `graphs/` | 三路径图定义：`migration.py`（Path-B CUDA/Triton 迁移）、`new_dev.py`（Path-C 全新开发） |
| `nodes/` | 节点实现：`common.py`（make_llm_node）、`hitl.py`（make_hitl_llm_node）、`delivery.py`（交付模式 + 框架适配）、`migration.py`（前端解析）、`validation.py`（compile/precision 真节点 + fix_loop 包装）、`micro_mod.py` |

设计理由：P0 单算子顺序执行场景下，约 50 行的 PhaseRunner 够用且更易调试。LangGraph 的复杂路由/分支条件留到 P1（选择性批量迁移）再评估引入。

### 任务管理层

`task_router/` 与 `task_store/` 在编排层之上提供多任务管理能力（一期-a 已落地，一期-b 规划中）：

- `task_router/commands.py`：显式命令逻辑（list / new / select / progress / run / complain），CLI 与 TUI 共用
- `task_router/executor_dispatch.py`：TaskRouter 按 `task.type`（develop / migrate / analyze / optimize）路由到执行器；一期-a 仅 develop（转 `op:` 调 PhaseRunner），其余 gated on Path A
- `task_store/store.py` + `models.py`：TaskStore（SQLite）+ Task 模型；`state` 由 rollup 实时推导（非 source of truth）
- `task_store/progress.py` + `rollup.py`：任务进展聚合 + 状态推导（draft/running/paused/done/failed）

### 记忆系统（四层）

1. **Working Memory** - 当前会话上下文（MemoryStore，`agent/memory.py`）
2. **Episodic Memory** - 完整会话历史（ChromaDB，`memory/episodic_memory.py`）
3. **Semantic Memory** - Skill 知识向量（SkillIndex + ChromaDB，`skills/index.py`）
4. **Procedural Memory** - 场景化工作流模板（编排器节点链）

## 7层Prompt结构

`agent/prompt_builder.py` 组装的系统 Prompt，从身份到上下文共七层：

1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory (Layer 5，跨节点由 `memory_pools` 持久化)
6. Skills Index（支持编排器 scope 注入 cannbot skill 包，PR-B R5b 路由 + R7 分组渲染）
7. Context Files + Timestamp + Env

## 双进程架构（TUI 模式）

Ascend Op Agent 支持 Hermes Agent 风格的双进程 TUI 交互界面：

```mermaid
graph TB
    subgraph TUI["TUI Frontend (Node.js + Ink)"]
        A[用户界面渲染] --> B[输入处理]
        B --> C[状态展示]
    end

    subgraph Backend["Agent Backend (Python) - Child Process"]
        D[JSON-RPC 解析] --> E[Agent 推理引擎]
        E --> F[工具调用]
        F --> G[审批交互]
        G --> D
    end

    A --> |"stdin/stdout JSON-RPC"| D
    C --> |"渲染更新"| A

    style TUI fill:#e1f5fe
    style Backend fill:#fff3e0
```

### 架构特点

- **TUI Frontend (Node.js + Ink)**
  - 始终保持响应式渲染
  - 处理用户输入和状态展示
  - 通过 stdin/stdout 与后端通信

- **Agent Backend (Python) - Child Process**
  - Agent 推理、工具调用、审批交互
  - 在后台线程池执行，不阻塞前端
  - 使用 JSON-RPC 2.0 协议通信

### 通信协议

```json
// 请求示例
{"jsonrpc": "2.0", "method": "invoke_tool", "params": {"name": "bash", "args": {...}}, "id": 1}

 // 响应示例
{"jsonrpc": "2.0", "result": {"output": "..."}, "id": 1}
```

### 启动流程

```mermaid
sequenceDiagram
    participant User
    participant TUI as TUI Frontend
    participant Agent as Agent Backend
    participant LLM as LLM API

    User->>TUI: 启动命令
    TUI->>Agent: 启动子进程
    Agent->>LLM: 初始化连接
    LLM-->>Agent: 连接成功
    Agent-->>TUI: 就绪
    TUI->>User: 显示交互界面
    User->>TUI: 输入请求
    TUI->>Agent: JSON-RPC 请求
    Agent->>LLM: 推理请求
    LLM-->>Agent: 推理结果
    Agent-->>TUI: JSON-RPC 响应
    TUI->>User: 更新显示
```