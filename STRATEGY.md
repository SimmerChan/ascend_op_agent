---
name: Ascend Op Agent
last_updated: 2026-05-10
---

# Ascend Op Agent Strategy

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

### Tool Discovery & Integration

MCP服务器支持、工具自注册、工具链组合能力，让Agent能自主发现和使用有用工具。

_Why it serves the approach:_ Agent使用工具自主完成任务，减少人工介入；同时工具可以向记忆系统写入经验。

### Workflow Orchestration

算子开发的全流程阶段跟踪、状态管理和检查点机制，确保多会话、多阶段的长程任务不丢失进度。

_Why it serves the approach:_ 让完整的5阶段流程能够在Agent自主驱动下完成，不因会话中断而中断。

### Skill Crystallization

将调试、性能优化的经验自动固化為可复用的skill，供后续算子开发参考。

_Why it serves the approach:_ 经验复用减少重复工作，让每次开发都能站在历史积累上。