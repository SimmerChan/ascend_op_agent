# 算子 Agent 重构决策级研究报告

> **用途**：给 go/no-go 决策用。覆盖三个子问题——方案建议合理性验证、LangGraph 多维对比、可行工作量估算。
>
> **证据基础**：4 路并行调研
> 1. LangGraph 官方文档 + 生产案例深度调研（Uber/Replit/Kalvium/Focused.io）
> 2. hermes-agent 源码考古（项目前身，0 框架依赖）
> 3. claude-code 源码考古（业界顶尖 agent，0 框架依赖）
> 4. 4 阶段重构方案的对抗性审查
>
> **日期**：2026-06-22

---

## 执行摘要（给 go/no-go 的结论）

**一句话结论：方案建议全部合理，但需要做一次关键反转——`workflow/` 是 3,759 行死代码，这不是"迁移"，是"新建"。基于此，LangGraph 的决策是"分层采用"，工作量是单人 8-12 周（不是原估 4 周）。**

三个子问题的快速结论：

| 子问题 | 结论 | 信心 |
|---|---|---|
| 方案建议是否可行、合理 | ✅ 合理，但 4 阶段顺序需倒置（Skills/沙箱提前）| 高 |
| 引不引入 LangGraph | ⚠️ **分层**：Phase 图用 LangGraph，Agent 核心不用 | 高 |
| 重构到完成要多少工作量 | 单人 8-12 周（原始 4 周是天真估计）| 中高 |

**关键反转**（最该知道的发现）：`workflow/` 的 9 个 Phase 从未被 `AIAgent`、`backend.py`、CLI、ACP 任何路径调用（grep 实证）。Phase3 是 `// TODO` 占位符，Phase4 写死 `success=True`，Phase5 用 `random.random()`，Phase8 用 `random.uniform()`。所以"从当前状态迁移"是个伪命题——**当前根本没有可用的算子开发流水线**，你在绿地上做选型。

---

## 第一部分：方案建议合理性验证

参考文档：`docs/brainstorms/op_agent_architecture_proposal.md`

### 1.1 三方证据交叉印证

| 方案论断 | claude-code 实证 | hermes-agent 实证 | 判定 |
|---|---|---|---|
| 刚性主流程 + 单 Agent | ✅ 纯 ReAct，无图框架 | ✅ 手写循环 + Kanban 工具 | 合理 |
| 渐进式 Prompt 加载 | ✅ plan mode + TodoWrite | ✅ coding_context 注入 | 合理 |
| Skills 是知识载体 | ✅ skills/bundled/ | ✅ 但 prompt-cache 神圣约束 | 合理 |
| Memory 双轨 | ✅ JSONL 单一真相源 | ✅ SQLite + eager persist | 合理 |
| HITL 关键节点审批 | ✅ permission mode 三层 | ✅ callback + gateway 两层 | 合理 |
| 沙箱隔离刚需 | ✅ seatbelt/bubblewrap | ✅ 6 种环境后端 | **合理且紧迫** |

**关键反证点**：claude-code 和 hermes-agent 都**不使用任何图框架**（grep 实证 0 命中 langgraph/langchain）。两者都是业界顶尖 agent，都用"ReAct 循环 + 持久化 transcript + 权限层"达到了目标。这直接挑战了"必须用 LangGraph"的隐含前提。

### 1.2 方案建议的 3 个硬伤（对抗性审查发现）

1. **阶段顺序倒置**：原方案把 Skills 放 Stage 3、沙箱放 Stage 4。但没 Skills 内容，Agent 会幻觉 AscendC API；没沙箱，`cann_compile` 会读 `~` 整个主目录。**Skills 应该 Stage 0，沙箱应该 Stage 1.5**。

2. **"真实工具调用"低估 5 倍**：Phase5 精度对比不是"调 python_exec"这么简单，需要：numpy 参考实现 + atol/rtol 策略 + 二进制序列化 + diff 展示。Phase4 的 `cann_compile` 需要真实 CANN 环境——而项目 `CLAUDE.md` 的环境要求里**根本没提 CANN**，说明当前开发机没有 NPU。

3. **"水平分层"是错的形状**：原方案先把所有 Phase 的基础设施搭好（Stage 1），再全部填真实逻辑（Stage 2），集成测试推迟到 Stage 4。这会在最危险的时刻才发现数据模型错了。应该用**垂直切片**：先让一个算子（如 Add）端到端跑通。

---

## 第二部分：LangGraph 多维对比（核心决策）

### 2.1 决定性发现——这决定了全部结论

`grep -rn "OperatorWorkflow|create_workflow" src/` 排除 `workflow/` 目录后 **0 命中**。3,759 行 workflow 代码是尸体（实证：`src/ascend_op_agent/backend.py`、`src/ascend_op_agent/agent/core.py:69-93`、`src/ascend_op_agent/cli.py:412-443` 都不 import `workflow/`）。因此：

- **Phase 图**：绿地选型，LangGraph vs 自研 vs 现状（无 Phase）
- **Agent 核心**：已有 145 行可用 ReAct 循环（`src/ascend_op_agent/agent/core.py:106-251`），不要动

### 2.2 十维对比表

| 维度 | LangGraph (Phase图) | 自研 StateGraph (复活 workflow/) | 纯 ReAct (现状) |
|---|---|---|---|
| **代码量**（9阶段+HITL+恢复）| ~200-350 行 | ~800-1200 行（+今日 3759 死代码）| 0（无 Phase）|
| **首版可用时间**（Phase0→3 spike）| 1 周 | 2-3 周 | 已存在但无 Phase |
| **加第 5/10 个阶段** | ~1 小时 | ~2-4 小时 | 不适用 |
| **状态持久化成本** | 2 行（换 checkpointer）| ~150-200 行（自研 serde+store+resume）| 0（仅内存，无恢复）|
| **可观测性成本** | ~0 行（2 个 env var 开 LangSmith）| ~50-100 行（自研 OTel）| 高（JSONL 只写不读）|
| **异步事件发射** | 原生（`stream_mode="updates"`）| 手写（扩 NotificationQueue）| 手写（现有 101 行队列）|
| **崩溃后恢复** | 原生（`invoke(None,config)`）| 未实现（需 ~200 行）| 不可能（内存历史）|
| **HITL 门禁** | 1 行（`interrupt_before=[...]`）| ~100-150 行（轮询+信号）| 未实现 |
| **学习曲线** | 中（reducer/interrupt/stream mode）| 低（纯 generator）| 最低（一个 while）|
| **依赖足迹** | +langgraph, langchain-core, xxhash（均 MIT，轻）| +0 | +0 |

### 2.3 LangGraph 的真问题（不是 LOC）

对抗性审查点出的 LangGraph 隐藏成本被调研证实：

- **序列化是真正的迁移阻塞**：`src/ascend_op_agent/workflow/models.py` 的 12 个 dataclass（`OpInfo`/`DesignDoc`/`CodeGenResult`...）**没有任何 round-trip 序列化**。要 checkpoint 必须先给它们写 `to_dict`/`from_dict` 或转 Pydantic——这是 ~150-250 行的硬性成本，不写 LangGraph 的 checkpointer 就吃不下。
- **checkpoint 写延迟**：Kalvium Labs 生产数据——精简 schema（<10KB）写入 <15ms；臃肿 schema（500KB+）暴涨到 300-800ms 成瓶颈。某项目曾因存 3MB checkpoint 导致 600ms 写入。**算子代码是会变大的**，这风险真实存在。
- **调试地狱集中在循环多 agent**：Reddit/r/LangChain 的"impossible to debug"抱怨**全部针对循环多 agent**。本项目是**无环顺序流水线**，正好是 LangGraph 的甜区，不在雷区。
- **混用 sync/async node 会竞态**（OpenInference #2190）。必须全 sync 或全 async，不能混。

### 2.4 三方参考实现的裁决

| 参考 | 用图框架？ | 持久化 | 多 agent | 给本项目的启示 |
|---|---|---|---|---|
| **claude-code** | ❌ 不用 | JSONL 单一真相源 | AgentTool 即工具 | ReAct + JSONL 足够强；可观测性靠 transcript |
| **hermes-agent** | ❌ 不用 | SQLite + eager persist（10 个埋点）| delegate_tool 工具 | SQLite checkpoint 模式可直接抄；**唯一缺的就是"硬性阶段转换"** |
| **LangGraph 生产用户** | ✅ Uber/Replit | SqliteSaver/PostgresSaver | supervisor/swarm | 无环顺序流水线是最佳应用场景 |

**hermes-agent 的关键原话**（这是最精准的裁决）：
> ascend_op_agent 是否引入 LangGraph，取决于一个问题：**它是否需要硬性、可声明的阶段转换**（保证 generate→compile→test→report 的 DAG，支持每阶段恢复）？如果是，这正是 hermes 缺失、LangGraph 干净提供的**唯一**功能。如果工作流是"LLM 选工具直到完成且状态可恢复"，hermes 的自研循环就够了，省去框架抽象税。

### 2.5 LangGraph 决策：分层采用

```
┌─────────────────────────────────────────────┐
│  Phase 图（parse→design→code→compile→test）  │  ← 采用 LangGraph
│  StateGraph + interrupt_before + SqliteSaver │
└──────────────────┬──────────────────────────┘
                   │ 作为单个节点调用
                   ▼
┌─────────────────────────────────────────────┐
│  Agent 核心（AIAgent.run_conversation）       │  ← 保持自研
│  145 行 ReAct + 6 个 httpx provider 适配器    │
└─────────────────────────────────────────────┘
```

**为什么不连 Agent 核心也用 LangGraph**：现有的 6 个 provider 适配器（OpenAI/Anthropic/Gemini/OpenRouter/Azure/Ollama，见 `src/ascend_op_agent/agent/providers/`）走的是非 LangChain 接口。改用 LangGraph 的 agent 抽象会逼你引入 `langchain-openai`/`langchain-anthropic` 包，丢掉自研适配器。投入产出比负的。

---

## 第三部分：可行工作量估算

### 3.1 原始 4 阶段方案 vs 现实校准

对抗性审查把原始估算（4 周）拆穿了。关键低估项：

| 原估 | 真实成本 | 低估原因 |
|---|---|---|
| Stage 1 "Orchestrator+Checkpoint 600行/1周"| **1500行/2周**（含测试）| 漏了 dataclass serde、async 协调、checkpoint 仓库后端选型 |
| Checkpointer "一个类" | 实际是"存储抽象+适配器+序列化+幂等"4 件套 | 2-3 天光写规格 |
| Stage 2 "真实工具调用" | 需要 numpy 参考实现+容差策略+序列化+CANN 环境 | 低估 5 倍 |
| 团队规模 | 隐含假设 1-2 人，实际 git 历史显示**单人**（u011801161）| 单人放大 2-3 倍 |

### 3.2 校准后工作量（单人，含测试）

| 方案 | 周数 | 第几周出可用产物 |
|---|---|---|
| 原始 4 阶段横向分层 | **8-12 周** | 第 6-8 周（且可能因 CANN 缺失而卡住）|
| **推荐：垂直切片 + 风险 spike** | **8.5 周** | **第 2 周末**（一个真实 Add 算子端到端跑通）|

### 3.3 推荐路线图（垂直切片）

| 阶段 | 周数 | 交付物 | 风险消除 |
|---|---|---|---|
| **Spike 0**：3 个最大未知数验证 | 1 周 | cann_compile 能否在开发机跑；numpy-kernel diff 格式；ChromaDB 检索质量 | 消除 25% 架构风险**在任何代码承诺前** |
| **S1 垂直切片**：1 个算子（Add）端到端 | 1.5 周 | 1 个 Skill + 简化 StateGraph(Phase0-5) + 真实 file_write/cann_compile/numpy 对比，无沙箱 | 第 2 周末有真实产物 |
| **S2 沙箱 + 白名单 + CPU 模式** | 2 周 | Docker 限制 + 命令白名单 + CANN 缺失时降级为模拟 | 让切片可无人值守运行 |
| **S3 Skills 内容扩充** | 1 周 | 写出 9 个 SKILL-*.md + 阶段感知加载 | Agent 不再幻觉 API |
| **S4 扩展到全部 9 阶段** | 2 周 | 以 S1 切片为模板复制 | — |
| **S5 Memory 四层接入** | 2 周 | 有真实对话数据后才 backfill | — |
| **S6 通知/Viewer/打磨** | 1 周 | 阶段级进度、TUI 审批渲染 | — |
| **合计** | **8.5 周** | — | 第 2 周即可演示 |

### 3.4 不应现在做的（YAGNI）

- **多 agent**：Phase 是顺序管道不是可路由专家，supervisor/swarm 解决本项目不存在的问题（Focused.io 量化：supervisor 多耗 20-40% token）
- **LangGraph Studio**：重且 buggy，占 10+GB，用浏览器版 LangSmith 即可
- **token 级流式**：Option B（async 原生）是 +2-3 周的独立决策，先用现有粗粒度进度

---

## 第四部分：Go/No-Go 决策标准

### 4.1 决策矩阵

| 你的回答 | 推荐方案 |
|---|---|
| "我要保证 compile 失败后能从该 Phase 恢复，不从头跑" | **采用 LangGraph 做 Phase 图**（`interrupt_before` + SqliteSaver 是它的甜区）|
| "我相信 LLM 能自己遵循 Prompt 里的 Phase 顺序" | **保持纯 ReAct**，但**立刻删除 3759 行死代码 workflow/**——现状（死代码 + 幻觉 Phase）是最差选项 |
| "我要自研但保证阶段硬性" | **复活 workflow/ 但加 SQLite checkpoint + 真实逻辑**，~800-1200 行，比 LangGraph 多 3-4 倍 LOC |

### 4.2 一周 Spike 的 Go/No-Go 标准（如果选 LangGraph）

做这个 1 周 spike 再决定：

1. `pip install langgraph langgraph-checkpoint-sqlite`
2. 定义 `OpState(TypedDict)`（只 Phase0-3 字段，用 dict 不用 dataclass 避开 serde）
3. 4 个 node + `interrupt_before=["design"]` + SqliteSaver
4. 跑通：invoke → 命中 interrupt → `invoke(None,config)` 恢复 → LangSmith 看到每节点 trace

**Go 标准**：恢复 + checkpoint round-trip + trace 三者都工作 → 推进
**No-Go**：serde 或 async 集成卡住 → 放弃 LangGraph，转自研（也是合法选择）

### 4.3 风险登记表（如果不引入 LangGraph）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 3759 行死代码 workflow/ 继续腐烂、误导贡献者 | 高 | 中 | **本 sprint 内要么引入 LangGraph 要么删 workflow/**——现状最差 |
| 无崩溃恢复：Phase4 compile 失败丢掉 Phase0-3 全部工作 | 高（NPU 编译常失败）| 高 | 至少把 `_context` 持久化到磁盘（穷人 checkpoint，~150 行）|
| 无阶段级可观测："为什么产生这个 design"无法回答 | 高 | 中 | 阶段边界加结构化日志（不如 LangSmith，但聊胜于无）|
| HITL 门禁只在 Prompt 散文里，不强制 | 高 | 高 | 至少在活代码路径实现 yield 确认 |
| 自研 NotificationQueue + ThreadPool 桥累积边界 bug | 中 | 中 | 今天能用但脆弱，留维护预算 |

---

## 第五部分：未被方案覆盖的隐藏风险

对抗性审查额外发现、原方案文档未提及的风险：

| 风险 | 说明 |
|---|---|
| **无迭代修复回路** | 算子开发现实中是多次编译失败→反馈→重生。原方案是"fire and forget"，无 repair loop，10 个算子可能只成 1 个 |
| **"能跑" ≠ "快"** | 方案停在"numpy 对得上"。未规划吞吐/带宽/occupancy/tiling benchmark。慢 100 倍的算子无用 |
| **跨算子知识迁移** | Memory 4 层声称帮助"下次"，但 ChromaDB 对话历史是检索不是迁移。真正迁移需要抽象（如"Add 需显式 broadcast"）|
| **TUI 的 `waiting_confirm` 状态未用** | 若加人工检查点，需指定位置 + 渲染内容（代码 diff/测试结果），当前只支持 yes/no |
| **安全与 IP** | 算子代码可能专有；LLM API 发代码到外部；Memory 持久化用户算子到 ChromaDB（本地？云？加密？）全部未定 |
| **LLM 成本未建模** | 9 阶段 + 修复回路可能 20-50 次 LLM 调用/算子，$0.20-5/算子。100 算子=$20-500。缓存是必需不是可选 |
| **API 风格未定** | Ascend 有 TIKE（旧）和 AscendC（新）+ CANN 高层 API。未声明目标，决定 Skill 内容和工具输出 |
| **"完成"未定义** | 全项目成功指标缺失："5 次迭代内产出可用 LayerNorm"？无指标则阶段无终点 |

---

## 最终建议

**三条可执行决策**：

1. **本周先做 Spike 0（1 周）**：验证 `cann_compile` 在开发机能否跑、numpy-kernel diff 格式、ChromaDB 检索质量。这 3 个未知数决定整个架构是否成立——**在任何代码承诺前先消除它们**。

2. **LangGraph 分层采用**：Phase 图用 LangGraph StateGraph（+1 周 spike 验证 serde），Agent 核心保持自研 145 行 ReAct。不要碰多 agent、不要碰 async 原生流式。

3. **抛弃横向 4 阶段，改垂直切片**：S1 先让一个 Add 算子端到端跑通（含真实 Skill + 简化 StateGraph + 真实工具 + 无沙箱），第 2 周末有真实产物。单人 8.5 周到完成。

**底线**：原方案**方向完全正确**，但执行细节需要三处校准——① 死代码不是迁移是新建 ② LangGraph 分层不全上 ③ 垂直切片替代横向分层。校准后工作量 8.5 周（不是 4 周），但第 2 周就有可演示产物。

---

## 附录：当前架构现状（代码实证）

### 关键文件 LOC 与状态

| 文件 | LOC | 状态 |
|---|---|---|
| `src/ascend_op_agent/workflow/`（8 文件）| 3,759 | **死代码**，从未被调用 |
| `src/ascend_op_agent/agent/core.py` | 608 | ReAct 循环在 106-251，可用 |
| `src/ascend_op_agent/agent/session_manager.py` | 386 | JSONL append-only，从不读回复用 |
| `src/ascend_op_agent/agent/session_record.py` | 268 | Entry dataclass，无 round-trip serde |
| `src/ascend_op_agent/agent/tool_registry.py` | 218 | 8 个工具，OpenAI 格式 |
| `src/ascend_op_agent/agent/prompt_builder.py` | 194 | 7 层 Prompt，但 Skills 层是占位 |
| `src/ascend_op_agent/agent/memory.py` | 205 | 仅 2 pool，四层 MemorySystem 未接入 |
| `src/ascend_op_agent/backend/rpc/server.py` | 190 | asyncio JSON-RPC |
| `src/ascend_op_agent/backend/rpc/agent_service.py` | 143 | ThreadPoolExecutor 桥 |
| `src/ascend_op_agent/backend/rpc/notification_queue.py` | 101 | sync→async 队列桥 |
| `src/ascend_op_agent/memory/`（ChromaDB 子系统）| 1,565 | 运行时基本未用 |

### Phase 桩实证（`src/ascend_op_agent/workflow/phases.py`）

| Phase | 实现 | 真实性 |
|---|---|---|
| Phase0Init | 正则 + 关键字字典 | ❌ 不调 LLM 不调工具 |
| Phase1Analysis | 复杂度评分 + 拼字符串 | ❌ 同上 |
| Phase2Design | 拼 DesignDoc | ⚠️ 唯一审批点，但只拼字符串 |
| Phase3CodeGen | `template.format(...)` | ❌ `// TODO: 实现算子逻辑` |
| Phase4Verify | 返回 `success=True` | ❌ 注释原话"桩实现，直接返回成功" |
| Phase5Precision | `random.random() > 0.1` | ❌ fake 90% 通过率 |
| Phase7SkillSave | 调 SkillSaver | ✅ 唯一落地 |
| Phase8Performance | `random.uniform()` | ❌ fake 性能数据 |

### 工具现状（`src/ascend_op_agent/agent/tools/`）

8 个工具模块（file_read/file_write/file_search/patch/shell_exec/python_exec/git/npu）已实现并与 Phase **完全断开**。Phase4 应调 `cann_compile`，Phase8 应调 `msop`，但它们都 hardcode 返回成功。

### 安全现状

无命令白名单、无工作目录限制、无 Docker/K8s 沙箱、无资源限制、无网络隔离。会话历史 S1651 实证：项目根目录曾出现字面 `~` 目录，`rm -rf ~` 可能误删主目录——当前缺乏沙箱的直接证据。

---

## 参考来源

### LangGraph / LangChain 官方
- LangGraph Streaming — https://docs.langchain.com/oss/python/langgraph/streaming
- LangGraph Persistence — https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Checkpoints reference — https://reference.langchain.com/python/langgraph/checkpoints
- LangGraph Observability (LangSmith) — https://docs.langchain.com/oss/python/langgraph/observability
- LangChain Release Policy — https://docs.langchain.com/oss/python/release-policy
- LangChain + LangGraph 1.0 announcement — https://www.langchain.com/blog/langchain-langgraph-1dot0

### 生产案例与基准
- Is LangGraph Used In Production? (Uber/Replit/LinkedIn/Elastic/AppFolio) — https://www.langchain.com/blog/is-langgraph-used-in-production
- Kalvium Labs: 6 production agents（checkpoint 延迟数字、失败模式）— https://www.kalviumlabs.ai/blog/langgraph-in-production-stateful-multi-step-agents/
- Focused.io: Supervisor vs Swarm 基准（延迟/token/路由准确率）— https://focused.io/lab/multi-agent-orchestration-in-langgraph-supervisor-vs-swarm-tradeoffs-and-architecture
- Augment Code: Swarm vs Supervisor（token delta 20-40%）— https://www.augmentcode.com/guides/swarm-vs-supervisor

### 批判性视角
- r/LangChain: production-ready? — https://www.reddit.com/r/LangChain/comments/1hqufg2/why_isnt_langchainlanggraph_productionready/
- r/LangChain: cycles hard to debug — https://www.reddit.com/r/LangChain/comments/1t1cyog/why_langgraph_cycles_are_hard_to_debug_with/
- r/LangChain: debug complex agents — https://www.reddit.com/r/LangChain/comments/1p6rna2/how_do_you_actually_debug_complex_langgraph/
- r/LocalLLaMA: deps = langchain-core only — https://www.reddit.com/r/LocalLLaMA/comments/1dxj1mo/langchain_bad_i_get_it_what_about_langgraph/

### GitHub Issues
- langchain-google#873: sync blocking in ASGI — https://github.com/langchain-ai/langchain-google/issues/873
- langgraph#6105: astream_events nested graphs — https://github.com/langchain-ai/langgraph/issues/6105
- OpenInference#2190: sync/async node 竞态 — https://github.com/Arize-ai/openinference/issues/2190

### 内部参考代码库
- hermes-agent — `/Users/huangshilei/Documents/pythonprojects/hermes-agent`（0 框架依赖，自定义 ReAct + SQLite checkpoint）
- claude-code — `/Users/huangshilei/Documents/pythonprojects/claude-code`（0 框架依赖，JSONL 单一真相源 + ReAct + plan mode）
