---
name: Ascend Op Agent
last_updated: 2026-07-29
---

# Ascend Op Agent Strategy

## 产品定位

Ascend Op Agent 是一个 **AI Agent 驱动的运行时引擎**（非通用 chatbot），专为昇腾 NPU 算子开发场景设计。它用**刚性状态机工作流**保证交付确定性，用 **Skill 知识层**沉淀昇腾领域专业知识，用 **910B 真编译 + ST 驱动真算子验证**确保算子质量。

支持三条用户旅程，共享同一套运行时后端（PhaseRunner 状态机 + CheckpointStore + NpuExecutor）：

| 路径 | 场景 | 优先级 | 状态 |
|:----:|------|:------:|:----:|
| **A** | 模型级选择性迁移（客户模型含 N 个 CUDA/Triton 算子） | P2 | 规划中 |
| **B** | 单算子迁移（CUDA / Triton 源代码） | P0 | ✅ 已交付 |
| **C** | 单算子全新开发（算子需求描述） | P0 | ✅ 已交付 |

**当前状态**：P0 + P1 计划已完成（2026-06-23 -> 2026-07-05），v1.0.0 发布于 2026-07-06，ship gate 4 步全过（lint + unit_test + N=20 stress 100% + e2e_tui 5/5）。后续迭代进展见 [CHANGELOG.md](CHANGELOG.md)。

## Target problem

GPU工程师迁移算子到AscendC时，每次对话都得从头开始，缺乏持久记忆；同时样板代码编写耗时数小时拖累开发迭代速度。

## Our approach

Agent维护一个持久化存储，工具可以读写其中，保证开发上下文能在不同会话间存活，经验可被固化复用——从而让算子开发的全流程（需求分析→方案设计→开发→测试→性能优化）不因上下文丢失而中断。

## Who it's for

**Primary:** GPU工程师迁移算子到AscendC - 他们雇佣这个产品来消除样板代码、加速开发迭代。

## Key metrics

- **Full workflow completion rate** - 完成全流程（需求分析→方案设计→开发→测试→性能优化）的比例；where: 需要埋点
- **Memory persistence rate** - Agent正确回忆同一算子前序会话上下文比例；where: 需要埋点
- **Tool call success rate** - 工具调用成功（不需人工介入）比例；where: 需要埋点
- **Skill reuse rate** - 调试/优化经验被固化为可复用skill比例；where: 需要埋点

## Tracks

### Persistent Memory System

持久化存储层，工具和Agent都可以读写，保证开发上下文在不同会话间存活。

_Why it serves the approach:_ 解决"缺乏持久记忆"这个核心难题，让全流程跟踪成为可能。

**当前状态**：✅ 基础设施已落地（四层记忆 Working/Episodic/Semantic/Procedural + ChromaDB 向量存储 + SessionRecordManager 持久化）。CheckpointStore（SQLite v2 + WAL）提供节点级崩溃恢复，跨会话状态可续跑。

### Tool Discovery & Integration

MCP服务器支持、工具自注册、工具链组合能力，让Agent能自主发现和使用工具。

_Why it serves the approach:_ Agent使用工具自主完成任务，减少人工介入；同时工具可以向记忆系统写入经验。

**当前状态**：✅ 已落地（MCP 客户端 + 工具注册表 + agent/tools/ 下 11 个工具：file_read/write/search、patch、shell_exec、python_exec、git_*、npu_smi、msop、skill_manage）。NpuExecutor 封装 SSH->docker exec ops_pt->build.sh 真编译工具链。

### Workflow Orchestration

算子开发的全流程阶段跟踪、状态管理和检查点机制，确保多会话、多阶段的长程任务不丢失进度。

_Why it serves the approach:_ 让完整的5阶段流程能够在Agent自主驱动下完成，不因会话中断而中断。

**当前状态**：✅ P0+P1 已完成（自研 PhaseRunner 状态机，13 节点顺序+条件+HITL；fix_loop review->fix->re-review 闭环；910B 真编译 + ST 驱动真算子验证）。任务管理层一期已落地（task_router + task_store + rollup 状态推导 + executor_dispatch 路由），支持 migrate/analyze/optimize/develop 四类任务。

### Skill Crystallization

将调试、性能优化的经验自动固化為可复用的skill，供后续算子开发参考。

_Why it serves the approach:_ 经验复用减少重复工作，让每次开发都能站在历史积累上。

**当前状态**：✅ PR-A + PR-B 已落地（PR-A：`/learn` 命令 + skill_manage 工具，5U scope；PR-B：R5b 路由 + R6 hybrid 检索 + R7 分组 + R3 self-check）。Skill Curator Lite Tier 0 已落地（技能活跃度埋点 + curator status 只读汇总）。cannbot-skills 加载器消费华为官方 skill 仓库（signal-1 跟踪加载/使用）。