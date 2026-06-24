---
title: "feat: 算子迁移/开发运行时引擎（自研状态机 + cannbot 集成）"
type: feat
status: active
date: 2026-06-23
origin: docs/brainstorms/selective_migration_mvp.md
---

# feat: 算子迁移/开发运行时引擎（自研状态机 + cannbot 集成）

## Summary

新增自研轻量状态机编排器（PhaseRunner + sqlite checkpoint），包裹现有 AIAgent ReAct 核心作为 LLM 节点，消费 cannbot-skills 作知识层，补 checkpoint/崩溃恢复/阶段化/统一验证/NPU 执行。P0 聚焦单算子闭环——路径 B 迁移（CUDA via cuda2ascend-simt + Triton via 5-skill 链）与路径 C 新开发，在 A5/950 上跑通"解析→迁移/实现→编译→验证"且崩溃可恢复。不引入 LangGraph，纯 stdlib + 现有 pydantic。

---

## Problem Frame

经过多轮 brainstorm（见 origin 及 `docs/brainstorms/build_vs_buy_strategy.md`、`op_agent_refactor_decision_report.md`），项目定位校准为"算子迁移/开发**运行时引擎**"——知识层全部消费华为官方 cannbot-skills（cuda2ascend-simt 已用 29 算子验证、triton 5-skill 链成熟、A5/950 是官方验证硬件），我们的价值在 skill 填不上的运行时层。

当前代码的两个核心问题：(1) `src/ascend_op_agent/workflow/`（3759 LOC）是**死代码**——从未被 backend/agent/CLI 任何路径调用；(2) `AIAgent.run_conversation` 是 145 行 ReAct 循环，无阶段、无崩溃恢复。本计划把死代码改造/替换为自研状态机编排器，包裹现有 ReAct 核心，补运行时能力。

定位转向的完整决策链见项目 memory（`positioning-pivot-to-runtime-engine`）。

---

## Requirements

- R1. 支持单算子迁移（路径 B）：CUDA 算子经 cuda2ascend-simt 迁移、Triton 算子经 5-skill 链迁移，产出可编译 AscendC 工程
- R2. 支持单算子新开发（路径 C）：从算子需求生成 AscendC kernel
- R3. cannbot-skills 作为知识层（git submodule 引入，不自研迁移/领域知识）
- R4. 阶段化执行 + checkpoint 崩溃恢复（编译失败不丢前面阶段成果）
- R5. HITL 关键节点（design 审批、交付模式 sample/torch_npu/pybind 选择）
- R6. A5/950 硬件上真实编译 + 精度/性能验证
- R7. 不引入 LangGraph/langchain 生态，自研轻量状态机（纯 stdlib + 现有 pydantic）

**Origin actors:** 算子开发工程师（内部，单人/小团队）
**Origin flows:** F1 模型级选择性迁移（P1，本计划外）、F2 单算子迁移（P0）、F3 单算子新开发（P0）

---

## Scope Boundaries

- 不做 P1：模型级 profiling、选择性决策、批量并行、30 卡池调度、闭环修复增强
- 不重写 cannbot skills（cuda2ascend-simt / triton 5-skill 链 / ops-spec 全部消费）
- 不引入 LangGraph / langchain / langchain-core / LangSmith
- 不做多租户隔离/计费、结构化审计签名、CI/CD 自动化、统一编译器 IR
- 不做新算子前端的复杂 LLM 路由（用 cannbot plugin 的 architect/developer/reviewer persona）

### Deferred to Follow-Up Work

- P1 模型级批量迁移（路径 A）：单独计划，依赖本 P0 的单算子闭环作为基础单元
- 多卡 NPU 池调度（30 卡共享/独占）：P1
- CUDA 迁移深度优化（cannbot 已覆盖基础，深化在 P1）

---

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/agent/core.py` — `AIAgent.run_conversation`（145 行 ReAct 循环），保留作为 LLM 节点内部调用；状态回调 `_status_callback`/`_tool_progress_callback` 可复用
- `src/ascend_op_agent/backend.py:151-201` `_setup_agent` — AIAgent 装配点，`_handle_run_conversation:69` 是 RPC 入口，编排器挂在此
- `src/ascend_op_agent/agent/session_record.py` — Entry/UserEntry/LLMEntry/ToolEntry 有**完整** `to_dict`/`from_dict`/`to_json`/`entry_from_json` round-trip（零新代码，直接复用进 checkpoint）
- `src/ascend_op_agent/workflow/models.py` — 12 个 dataclass，仅 CodeGenResult 有（且丢 content 的）to_dict，**6 个需补 serde**（U5）
- `src/ascend_op_agent/agent/tools/` — cann_compile/msop/npu_smi/file_write/python_exec 等 8 工具，复用
- `src/ascend_op_agent/backend/rpc/notification_queue.py` — 现有 sync→async 通知桥，复用推阶段事件
- `src/ascend_op_agent/config.py:112` SessionConfig — CheckpointConfig 作为平级新增

### Institutional Learnings

- hermes-agent（项目前身）：证明自定义 ReAct + SQLite checkpoint + ThreadPoolExecutor 工具执行可构建强 agent，0 框架依赖
- claude-code：证明 JSONL 单一真相源 + ReAct + plan mode 足够，0 框架依赖。两者都不用任何图框架

### External References

- cannbot-skills: https://gitcode.com/cann/cannbot-skills（cuda2ascend-simt SKILL.md 已读，3 交付模式 + YAML 规则库 + validation gate，29 算子验证）
- 决策依据见 `docs/brainstorms/build_vs_buy_strategy.md`、`op_agent_refactor_decision_report.md`

---

## Key Technical Decisions

- **自研轻量状态机，不用 LangGraph**：顺序单算子管道是简单 DAG 甜区，LangGraph 的条件路由/多 agent/fan-out 全用不上；langchain-core 是大依赖（版本破坏性变更多）；dataclass serde 工作量两边一样，LangGraph 只省 ~150 行 checkpointer，代价不值得。纯 stdlib（sqlite3）+ 现有 pydantic，零新依赖
- **编排器挂在 backend.py 与 AIAgent 之间**：PhaseRunner 作为新层，LLM 节点内构造 fresh AIAgent（session_manager=None，编排器 owns 持久化）、从 checkpoint **rehydrate** `agent._conversation_history`、同步调 run_conversation
- **OpState TypedDict + dataclass 以 dict 嵌入**：SqliteSaver/checkpoint 要求 JSON 可序列化，dataclass 存为 dict，节点内用 `from_dict` 重建
- **cannbot hybrid 集成**：编排层显式 phase→agent→skill 映射（决策表），phase 内 LLM 读 skill `description` 触发词路由选 skill。保留 cannbot 目录结构（`@references/*.md` 相对路径）
- **路径 B 自建迁移 prompt，路径 C 复用 cannbot persona**：cannbot 的 architect/developer/reviewer 是为新算子开发写的，迁移需自建 scoping prompt
- **workflow/ 死代码分层处理**：保留 models.py（加 serde 后迁 orchestrator/models.py，旧路径 re-export 保兼容）+ compiler.py/performance.py（subprocess 封装，U13 复用）；删除 engine/phases/adapters/skill_save（U8 最后做，避免中途破坏测试）
- **对话缓冲与审计分离**：`session_record.Entry` 的 round-trip 仅用于**审计日志**（Viewer/历史）；编排器的**对话缓冲**用简单 `list[{"role","content"}]` 直接存 OpState.messages（与 AIAgent `_conversation_history` 同形，零转换）。Entry shape 与对话缓冲不同形，不可复用进缓冲
- **PromptBuilder 加 scoping hook**：现有 `_build_skills_layer` 硬编码，无注入点。新增 `skills_layer_override: Optional[str]` 参数透传到 `build_system_prompt`，节点用它注入该阶段 cannbot skill 包（~10 LOC delta）
- **补 6 个 dataclass serde**：workflow/models 的 OpInfo/DesignDoc/CodeGenResult/CompileResult/PrecisionReport/PhaseResult 补 to_dict/from_dict + Enum 强制（嵌入 OpState 作 dict）

---

## Open Questions

### Resolved During Planning

- LangGraph vs 自研：选自研（见 Key Technical Decisions + 用户确认少依赖偏好）
- checkpoint 后端：sqlite3 stdlib 单文件，3 表（checkpoints/artifacts/pending_approvals）
- cannbot 集成方式：git submodule + hybrid 映射，不全量采用 plugin 格式（避免变 Claude Code 插件消费者）

### Deferred to Implementation

- 6 个 dataclass 的精确字段序列化（Enum 边界、嵌套递归深度）—— U5 实现时定
- 迁移前端 prompt 的精确措辞 —— U10/U11 实现时迭代
- cannbot submodule pin 的具体 commit —— U1 执行时选最新验证版

---

## Output Structure

```
src/ascend_op_agent/orchestrator/
├── __init__.py
├── state.py              # OpState TypedDict + 自定义 reducer
├── state_machine.py      # PhaseRunner（节点/边/条件/暂停）
├── checkpoint.py         # CheckpointStore（sqlite3）
├── cannbot_loader.py     # cannbot skill 加载器（保留 @references）
├── precision_diff.py     # numpy-kernel 精度对比协议
├── npu_exec.py           # NpuExecutor（合并 compiler+performance）
├── context_compress.py   # transcript 压缩
├── models.py             # 从 workflow/models.py 迁移 + serde
├── nodes/
│   ├── common.py         # make_llm_node 工厂 + phase 事件
│   ├── validation.py     # compile/precision 确定性节点
│   ├── fix_loop.py       # 闭环修复
│   └── delivery.py       # 交付模式选择
├── graphs/
│   ├── new_dev.py        # 路径 C
│   └── migration.py      # 路径 B（CUDA/Triton）
└── prompts/
    ├── new_dev/*.md      # 路径 C（从 cannbot persona 适配）
    └── migration/*.md    # 路径 B（自建）

vendor/cannbot-skills/    # submodule
tests/conftest.py         # 新建：hardware marker
tests/unit/orchestrator/  # serde/state/loader/fix_loop 单测
tests/integration/        # 编排器/路径/崩溃恢复集成测
```

---

## High-Level Technical Design

> *方向性设计示意，供评审，非实现规格。*

```
RPC agent.run
   │
   ▼
AgentAsyncWrapper ── 有编排器时委托 ──▶ Orchestrator.invoke(user_input, thread_id)
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                             ▼
            CheckpointStore                              PhaseRunner
            (sqlite3)                                    (节点列表 + 顺序/条件执行)
            load(thread_id) ── resume ──▶                   │
                                                         每个 LLM 节点:
                                                         ┌─ scoped PromptBuilder (Layer 6 注 cannbot skill)
                                                         ├─ fresh AIAgent(session_manager=None)
                                                         ├─ rehydrate _conversation_history from state
                                                         ├─ run_conversation(task_prompt)
                                                         └─ return state_update
                                                         确定性节点:
                                                         ┌─ compile/precision: 调 NpuExecutor
                                                         └─ delivery: HITL → pending → 暂停
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                             ▼
            每节点后 save(thread_id)                    NotificationQueue
            (sqlite transaction)                      → orchestrator.progress
```

PhaseRunner 的暂停/恢复：节点返回 `{"interrupt": payload}` → PhaseRunner 标记 `waiting_confirm` + 存 pending → 返回给调用方 → `resume(thread_id, reply)` 时 load + 继续。

---

## Implementation Units

### Phase 1: Spike 0 — 集成验证（1 周）

- U1. **cannbot-skills submodule + skill 加载器**

**Goal:** 将 cannbot-skills 引入为 submodule，实现保留 `@references` 相对路径的 skill 加载器

**Requirements:** R3

**Dependencies:** 无

**Files:**
- Create: `.gitmodules`, `vendor/cannbot-skills/`（submodule）, `src/ascend_op_agent/orchestrator/__init__.py`, `src/ascend_op_agent/orchestrator/cannbot_loader.py`
- Test: `tests/unit/orchestrator/test_cannbot_loader.py`

**Approach:**
- `git submodule add` cannbot-skills 到 vendor/，pin 已验证 commit
- `load_skill(skill_dir)` 解析 YAML frontmatter（name/description/body/base_dir）
- `resolve_reference("@references/foo.md")` 相对 base_dir 解析，保留目录结构

**Patterns to follow:** 现有 `src/ascend_op_agent/skills/index.py` 的 SkillIndex（但 cannbot 用独立加载器，格式不同）

**Test scenarios:**
- Happy path: 加载 cuda2ascend-simt，frontmatter 解析正确
- Integration: `@references/*.md` 解析到非空内容且路径正确
- Edge case: 缺失 frontmatter 的 skill 优雅降级

**Verification:** `load_skill` 能加载三个代表 skill（cuda2ascend-simt/triton-op-coding/ascendc-tiling-design），references 解析非空

---

- U2. **A5/950 编译/精度冒烟 + 硬件测试基础设施**

**Goal:** 验证 cann_compile/msop 在 A5/950 真实可用；建立 numpy-kernel 精度对比协议；建立硬件测试 gate

**Requirements:** R6

**Dependencies:** 无（U1 并行）

**Files:**
- Create: `tests/conftest.py`（hardware marker + ASCEND_HOME_PATH 跳过）, `tests/integration/test_a5950_compile_smoke.py`, `tests/fixtures/trivial_kernel/`, `src/ascend_op_agent/orchestrator/precision_diff.py`, `tests/integration/test_precision_diff_smoke.py`

**Approach:**
- `@pytest.mark.hardware` marker，`ASCEND_HOME_PATH` 未设自动跳过
- `compute_diff(golden, actual)` 算 abs_err/rel_err/cos_sim/max_err；`meets_requirement(thresholds)`
- 复用现有 `agent/tools/npu_tool.cann_compile`

**Execution note:** 硬件测试先写（characterization），确认 cann_compile 在目标硬件可用

**Test scenarios:**
- Happy path: trivial kernel 在 A5/950 编译通过，产物存在
- Happy path: 已知 golden+actual 算出预期 cos_sim
- Error path: 离硬件时测试干净跳过（非失败）
- Integration: 硬件上对编译产物端到端精度对比

**Verification:** A5/950 上 `pytest -m hardware` 通过；离硬件跳过。**Spike 0 硬门槛——失败则硬件假设错，P0 不启动**

---

- U3. **cannbot skill 路由质量冒烟**

**Goal:** 验证 cannbot skill 的 description 触发词在 LLM 路由下质量足够（支撑 hybrid 集成的 phase 内路由）

**Requirements:** R3

**Dependencies:** U1

**Files:**
- Create: `tests/integration/test_skill_routing_smoke.py`

**Approach:** 3 个阶段任务 prompt + 3 个 skill description，调 `LLMClient` 选最佳匹配，断言 ≥2/3 正确

**Test scenarios:**
- Happy path: 3 个代表阶段各路由到正确 skill（≥2/3）
- Fallback: 失败则降级为硬编码 phase→skill 映射表（非阻塞，主机制已是显式映射）

**Verification:** ≥2/3 路由正确，或记录为"用硬编码映射兜底"

---

### Phase 2: P0 Foundation — 自研状态机（~2 周）

- U4. **CheckpointConfig + 自研 CheckpointStore**

**Goal:** sqlite3 checkpoint 存储，3 表 schema，原子写入，resume 语义

**Requirements:** R4, R7

**Dependencies:** 无

**Files:**
- Modify: `src/ascend_op_agent/config.py`（加 `CheckpointConfig`：db_path/auto_resume，~line 117/144）
- Create: `src/ascend_op_agent/orchestrator/checkpoint.py`
- Test: `tests/unit/orchestrator/test_checkpoint.py`

**Approach:**
- sqlite3 单文件 `~/.ascend_op_agent/checkpoints.db`，3 表：checkpoints(thread_id/current_phase/state_json/status/updated_at)、artifacts(thread_id/phase/key/path/sha256)、pending_approvals(thread_id/phase/payload_json)
- `CheckpointStore.save/load/list_pending/mark_waiting/resume`，sqlite transaction 原子写入
- 序列化：OpState 直接 json；dataclass 走 to_dict（U5）；对话历史复用 session_record Entry serde

**Technical design:** *(方向性)* save 用 `BEGIN/COMMIT`，crash 不留半截；artifacts 表只存路径+sha256，大对象不进 json

**Test scenarios:**
- Happy path: save→load round-trip 状态完整恢复
- Edge case: 同 thread_id 多次 save（upsert 正确）
- Error path: 写入中断（模拟）不产生半截记录
- Integration: list_pending 正确列出未完成 thread

**Verification:** `python -c "from ascend_op_agent.config import Config; print(Config().checkpoint.db_path)"`；save/load round-trip 单测通过

---

- U5. **dataclass serde（嵌入 OpState）**

**Goal:** 给 6 个 workflow dataclass 加 to_dict/from_dict + Enum 强制，支持 checkpoint 序列化

**Requirements:** R4

**Dependencies:** 无

**Files:**
- Modify: `src/ascend_op_agent/workflow/models.py`（OpInfo/DesignDoc/CodeGenResult/CompileResult/PrecisionReport/PhaseResult）
- Test: `tests/unit/orchestrator/test_models_serde.py`

**Approach:**
- OpInfo：migration_strategy Enum 强制 .value
- DesignDoc：递归 op_info + arch_mapping
- CodeGenResult：**修复现有丢 content 的 to_dict**，加 from_dict
- CompileResult/PrecisionReport/PhaseResult：加 to_dict/from_dict（PrecisionReport 递归 test_results，PhaseResult 强制 status）

**Test scenarios:**
- Happy path: 6 个类 `X.from_dict(x.to_dict()) == x` round-trip 全过
- Edge case: CodeGenResult.to_dict 现含 files[*].content（显式断言）
- Edge case: Enum 字段 round-trip 后类型正确

**Verification:** 6 个 round-trip 单测全绿

---

- U6. **OpState + 自研 PhaseRunner 状态机骨架** ⭐ 垂直切片里程碑

**Goal:** OpState TypedDict + PhaseRunner（节点/边/条件/暂停）+ 最小 2 节点图验证架构

**Requirements:** R4, R7

**Dependencies:** U4, U5

**Files:**
- Create: `src/ascend_op_agent/orchestrator/state.py`, `src/ascend_op_agent/orchestrator/state_machine.py`, `src/ascend_op_agent/orchestrator/nodes/common.py`
- Test: `tests/unit/orchestrator/test_opstate.py`, `tests/integration/test_orchestrator_smoke.py`

**Approach:**
- OpState（state.py）：TypedDict，字段 thread_id/op_info/design_doc/code_result/compile_result/precision_report（dict|None）+ messages（`list[{"role","content"}]`，自定义累加 reducer，与 AIAgent `_conversation_history` 同形）+ memory_pools（dict，跨节点持久化）+ phase_history + current_phase + retry_counts + pending_confirmation + delivery_mode
- PhaseRunner（state_machine.py）：节点列表 + 顺序/条件边；节点是 `node(state)->dict`；节点返回 `{"interrupt":payload}` 则暂停存 pending；每节点后 save checkpoint；异常存 checkpoint+上报
- **节点执行契约（make_llm_node，对抗性评审 P0-1/P0-2 修正）：**
  1. PromptBuilder 经 U6 新增的 `skills_layer_override` 参数注入该阶段 cannbot skill 包（现有硬编码 Layer 6 无法 scope）
  2. fresh AIAgent(session_manager=None)；入口把 `state["messages"]` 直接赋给 `agent._conversation_history`（同形，零转换；**不复用 Entry**）
  3. 入口把 `state["memory_pools"]` rehydrate 到 `agent.memory`（否则 Layer 5 跨节点失效）
  4. 每节点调用是**新 ReAct 循环**——`run_conversation` 会把 task_prompt append 为新 user turn + 重置 iteration counter（core.py:121-132），这是预期行为，阶段间靠 messages 累加传上下文，不是续未完成的 tool loop
- **节点 idempotency 契约（P0-3 修正）：** 确定性节点（compile/precision）可重跑；LLM 节点在主副作用后 checkpoint，resume 时按 artifacts 表 sha256 跳过已落盘产物（避免重生成不同代码覆盖）；crash 中途的节点从入口重跑
- **resume 粒度：编排器节点级**——cannbot skill 内部步骤（如 cuda2ascend-simt 的 Step 0-7）不可单独恢复，crash 中途整个节点重跑
- 冒烟图：entry → echo_design LLM 节点 → done

**Execution note:** 先写集成测试验证"节点包裹 AIAgent + checkpoint 持久 + messages 累加"三个架构断言

**Technical design:** *(方向性)* PhaseRunner 是同步的（契合现有 ThreadPoolExecutor）；不引入异步框架

**Test scenarios:**
- Happy path: `orchestrator.invoke("design trivial add op", thread_id="t1")` 跑通，messages 非空、current_phase 前进、checkpoint 有行
- Integration: 第二节点入口 AIAgent._conversation_history 正确等于 state["messages"]（同形校验）
- Integration: memory_pools 在第二节点 rehydrate 后 Layer 5 非空（防跨节点丢失）
- Integration: PromptBuilder 的 skills_layer_override 实际注入了该阶段 skill 文本（dump system prompt 校验）
- Edge case: 节点返回 interrupt → status=waiting_confirm，pending 存入
- Error path: LLM 节点 crash 后 resume，artifacts 表 sha256 gate 跳过已落盘产物（不重生成覆盖）

**Verification:** 垂直切片端到端跑通。**架构验证里程碑——之后全是填节点**

---

- U7. **backend 接线 + resume 检测** ⭐ 最危险任务

**Goal:** 编排器挂入 backend.py；启动时检测 pending checkpoint 并 resume

**Requirements:** R4

**Dependencies:** U6

**Files:**
- Modify: `src/ascend_op_agent/backend.py`（`_setup_agent:151` 实例化 Orchestrator+CheckpointStore；`_handle_run_conversation:69` resume 检测）, `src/ascend_op_agent/backend/rpc/agent_service.py`（AgentAsyncWrapper 加 orchestrator 委托分支）, `src/ascend_op_agent/backend/rpc/server.py`（注册 session.list_pending/session.resume_with_input）

**Approach:**
- `_setup_agent`：实例化 `CheckpointStore(config.checkpoint)` + `Orchestrator`
- `_handle_run_conversation`：auto_resume 且有 pending thread → `orchestrator.invoke(None, thread_id)`；否则新 thread
- AgentAsyncWrapper.run_conversation_async：有 orchestrator 时走 orchestrator.invoke（线程池内），通知走同一 NotificationQueue

**Test scenarios:**
- Integration: 跑到 design 节点 → 模拟 crash（update 到 mid-graph）→ invoke(None) resume 到 design
- Happy path: 启动时 list_pending 检测到单个 pending，自动 resume
- Edge case: 多个 pending → 暴露 list_pending RPC，不自动选
- Error path: resume 时 checkpoint 损坏 → 清晰报错不静默

**Verification:** 手动——跑 op 到 design，kill -9，重启空输入 → 从 design 续（日志 "resuming thread..."）

**R4 与 Fallback 的诚实处理（对抗性评审 P0-3）：** resume（崩溃恢复）是 R4 的核心，P0 目标是让它真实工作。Fallback（resume flaky 时退到独立 `op.run` RPC、交互路径走原 AIAgent）是**降级路径**：若触发，R4 在 P0 仅以 opt-in 形式交付，必须在 P1 把 resume 补到 `agent.run` 主路径才算完整满足。不允许把 fallback 当作 P0 的常态——那等于 descope R4。

**resume 粒度（P1-3 修正）：** 编排器节点级恢复；cannbot skill 内部步骤不可单独 resume（crash 中途整个节点重跑，LLM 节点靠 artifacts sha256 gate 保护已落盘产物）。

---

- U8. **阶段通知 + 死代码清理**

**Goal:** 节点 emit phase 事件经 NotificationQueue 推前端；删除 workflow 死代码

**Requirements:** R4

**Dependencies:** U6, U7

**Files:**
- Modify: `src/ascend_op_agent/backend/rpc/agent_service.py`（phase_callback → orchestrator.progress 通知）, `src/ascend_op_agent/orchestrator/nodes/common.py`（emit phase started/completed/failed）
- Delete: `src/ascend_op_agent/workflow/{engine,phases,adapters,skill_save}.py`
- Modify: `src/ascend_op_agent/workflow/__init__.py`（仅 re-export models）, `src/ascend_op_agent/orchestrator/models.py`（迁入，旧路径 re-export）
- Migrate: `tests/test_workflow.py`/`test_adapters.py`/`test_skill_save.py`（迁移或删除）

**Approach:** make_llm_node 包裹：入口 emit phase_callback(phase,"started")，出口 ("completed",result)，异常 ("failed",err)；models.py 内容移到 orchestrator/models.py，旧路径 re-export 保兼容

**Test scenarios:**
- Integration: 节点执行时前端收到 orchestrator.progress 通知（started+completed）
- Happy path: `pytest tests/ -q` 绿（迁移测试通过、删除的移除）
- Regression: 旧 `from ascend_op_agent.workflow.models import X` 仍可用（re-export）

**Verification:** 全测试套件绿；手动跑显示前端阶段通知

---

### Phase 3: P0 Path C — 单算子新开发（~1 周）

- U9. **Path-C 新开发图 + HITL**

**Goal:** 单算子新开发完整图（analyze→design→codegen→review_fix→compile→precision），复用 cannbot persona，含 HITL

**Requirements:** R2, R3, R5

**Dependencies:** U6, U8, U1

**Files:**
- Create: `src/ascend_op_agent/orchestrator/graphs/new_dev.py`, `src/ascend_op_agent/orchestrator/prompts/new_dev/*.md`（从 vendor persona 适配）, `tests/integration/test_new_dev_graph.py`

**Approach:**
- 节点用 cannbot architect/developer/reviewer persona 作 scoped prompt + 对应 skill 包
- 条件边：review_fix→codegen（有问题且 retry<3）否则→compile；compile→compile_fix（失败且 retry<3）否则→precision
- design 节点 interrupt 做 HITL；session.resume_with_input(thread_id,payload) 续

**Patterns to follow:** cannbot plugins-official/ops-direct-invoke 的 architect/developer/reviewer persona + AGENTS.md 工作流

**Test scenarios:**
- Happy path: mock cann_compile 跑通 trivial RMSNorm，所有阶段访问、precision_report 存在、checkpoint 持久
- Integration: design 中断→批准→续到 codegen
- Edge case: review 发现问题→回 codegen 修复（retry 计数）
- Error path: compile 失败超 max retry → status=failed 清晰

**Verification:** mock 编译下 trivial 算子端到端跑通

---

### Phase 4: P0 Path B — 单算子迁移（~1.5 周）

- U10. **CUDA 前端节点（via cuda2ascend-simt）**

**Goal:** CUDA 算子迁移入口，消费 cuda2ascend-simt 产出 OpInfo + 架构映射

**Requirements:** R1, R3

**Dependencies:** U6, U1

**Files:**
- Create: `src/ascend_op_agent/orchestrator/graphs/migration.py`, `src/ascend_op_agent/orchestrator/prompts/migration/cuda_frontend.md`, `tests/integration/test_migration_cuda.py`

**Approach:**
- build_migration_graph(source_type, checkpointer)
- CUDA frontend_parse 节点 scoped skill=[cuda2ascend-simt]（root+references），prompt 指示产出 OpInfo(migration_strategy=CUDA_TO_ASCENDC) + ArchitectureMapping
- 输出入 state["op_info"]、state["design_doc"].arch_mapping

**Test scenarios:**
- Happy path: 喂小 CUDA kernel，断言 migration_strategy==CUDA_TO_ASCENDC、arch_mapping.mappings 非空
- Edge case: 缺失依赖的 CUDA 代码（用 skill 的降级分类 downgrade/blocked）
- Integration: 后续 design/codegen 节点能消费 frontend 产出

**Verification:** CUDA kernel 迁移产出正确 OpInfo + 映射

---

- U11. **Triton 前端节点（via 5-skill 链）**

**Goal:** Triton 算子迁移入口，路由 cannbot 5-skill 链

**Requirements:** R1, R3

**Dependencies:** U10

**Files:**
- Create: `src/ascend_op_agent/orchestrator/prompts/migration/triton_frontend.md`, Modify: `src/ascend_op_agent/orchestrator/graphs/migration.py`
- Test: `tests/integration/test_migration_triton.py`

**Approach:** Triton frontend_parse 加载 5-skill 链入口；LLM 读 description 触发选链中下一 skill（hybrid 路由）；产出同形 OpInfo(TRITON_TO_ASCENDC)

**Test scenarios:**
- Happy path: 喂 Triton 片段，断言 strategy==TRITON_TO_ASCENDC + 至少引用一链中 skill
- Edge case: 链中某 skill 无匹配 → 优雅降级

**Verification:** Triton 片段迁移产出正确 OpInfo

---

- U12. **交付模式选择节点**

**Goal:** sample/torch_npu/pybind 决策节点，HITL 暴露，两图共用

**Requirements:** R5

**Dependencies:** U9, U10, U11

**Files:**
- Create: `src/ascend_op_agent/orchestrator/nodes/delivery.py`, Modify: graphs/new_dev.py + graphs/migration.py（precision 后接入）

**Approach:** delivery_mode_node 启发式（有 torch_npu import→推荐 torch_npu；纯 kernel→sample）+ interrupt HITL；resume 后设 state["delivery_mode"] 路由到 framework_adapt 或 done

**Test scenarios:**
- Happy path: 节点 emit pending_confirmation 含 3 选项 + 推荐
- Integration: resume "torch_npu" 设状态并路由到 adapt
- Edge case: 无 HITL（headless）→ 默认 sample

**Verification:** 交付模式选择 + resume 路由正确

---

### Phase 5: P0 Unified Backend（~1 周）

- U13. **验证节点 + 轻量 NPU 执行封装**

**Goal:** 编译/UT/ST/精度确定性节点 + NpuExecutor 统一接口（合并 compiler+performance）

**Requirements:** R6

**Dependencies:** U6, U2

**Files:**
- Create: `src/ascend_op_agent/orchestrator/nodes/validation.py`, `src/ascend_op_agent/orchestrator/npu_exec.py`（合并自 workflow/compiler.py + workflow/performance.py）
- Test: `tests/unit/orchestrator/test_npu_exec.py`, `tests/integration/test_validation_nodes.py`

**Approach:**
- compile_node（调 cann_compile 建 CompileResult）、precision_node（msop UT + numpy golden diff 建 PrecisionReport）
- NpuExecutor: compile/run_ut/run_precision/profile；结果归档 {thread_id}/{phase}_{timestamp}.json

**Test scenarios:**
- Happy path: trivial kernel 编译 + precision 报告 ≥1 结果（硬件）
- Integration: compile 失败 → CompileResult 记录错误，节点返回需修复信号
- Error path: mock subprocess 验证错误处理

**Verification:** hardware-gated 集成测试编译+精度通过

---

- U14. **闭环修复控制器**

**Goal:** review→fix→re-review 有界循环 + transcript 压缩

**Requirements:** R4

**Dependencies:** U13

**Files:**
- Create: `src/ascend_op_agent/orchestrator/nodes/fix_loop.py`, `src/ascend_op_agent/orchestrator/context_compress.py`
- Test: `tests/unit/orchestrator/test_fix_loop.py`

**Approach:**
- run_fix_loop(state, kind, max_rounds=5)：停止条件=review clean 或 max_rounds 或确定性"不可修复"
- compress_transcript(messages, keep_last_n=4)：丢中间工具结果控 token

**Test scenarios:**
- Happy path: 注入可修复错误 → ≤3 轮收敛
- Error path: 注入不可修复错误 → max_rounds 终止 status=failed + 清晰消息
- Edge case: 压缩后保留 system + 最近 N 轮，中间工具结果丢弃

**Verification:** 可修复收敛 + 不可修复有界终止

---

## System-Wide Impact

- **Interaction graph:** Orchestrator 挂在 backend.py 与 AIAgent 之间；现有 agent.run RPC 行为可选委托给编排器；新增 session.list_pending/resume_with_input RPC
- **Error propagation:** 节点异常 → 存 checkpoint + phase_callback("failed")；不静默吞错；resume 时 checkpoint 损坏清晰报错
- **State lifecycle risks:** checkpoint 写入用 sqlite transaction（原子）；大对象走 artifacts 表不进 json；pending_approvals 需在 resume 后清理避免悬挂
- **API surface parity:** 现有 agent.run 行为保留（U7 fallback）；orchestrator 是叠加而非替换
- **Integration coverage:** 跨层场景——编排器→AIAgent rehydrate→工具执行→checkpoint→通知，单测无法覆盖，需集成测试（U6/U7/U9）
- **Unchanged invariants:** AIAgent.run_conversation 签名不变；现有 8 工具不变；backend RPC 协议兼容

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| backend 接线+resume 触碰唯一可用路径（U7 最危险）| Fallback：交互路径保留 AgentAsyncWrapper→AIAgent 不变，orchestrator 仅 op.run RPC 走，resume opt-in |
| 死代码删除破坏 4 个测试（U8）| 最后做；旧路径 re-export 保兼容；单 commit 可独立 revert |
| cannbot skill description 路由质量（U3）| 失败降级为硬编码映射表（主机制已是显式映射），非阻塞 |
| 硬件访问（U2 canary）| cann_compile 在 A5/950 失败则硬件假设错，P0 前停下重评 |
| 自研 checkpoint 并发（多 op 并行，P1 才有）| P0 单算子顺序，无并发；P1 加线程锁，sqlite WAL 模式 |

---

## Phased Delivery

```
Week1 Spike0:   U1 → (U2 || U3)
Week2 Found:    U4, U5 ||  → U6 ⬅ 垂直切片demo
Week3 Found:    U7 → U8
Week4 PathC:    U9
Week5 PathB:    U10 → U11 → U12
Week6 Backend:  U13 → U14
Week7 buffer:   集成加固、硬件测试、文档
```

U6 是架构验证里程碑（自研状态机 + checkpoint + AIAgent 包裹），之后全是增量填节点。P0 完成验收：路径 B（CUDA+Triton）+ 路径 C 各跑通一个算子，崩溃恢复有效。

---

## 对抗性评审修正记录（2026-06-23 doc-review）

ce-adversarial-document-reviewer 评审发现 3 个 P0（计划假设了源码不支持的能力）+ 4 个 P1。P0 已直接修正进 U6/Key Decisions/U7；P1 处理如下：

### 已修正（P0）
- **P0-1 PromptBuilder 无 scoping hook**：Key Technical Decisions + U6 新增 `skills_layer_override` 参数，节点用它注入 cannbot skill 包
- **P0-2 AIAgent 包裹契约三处断裂**：对话缓冲用 `list[{"role","content"}]` 直接存（不复用 Entry）、memory_pools 也 checkpoint、每节点是新 ReAct 循环（非续 loop）——U6 节点执行契约已重写
- **P0-3 resume 语义**：U6 加节点 idempotency 契约（artifacts sha256 gate）；U7 明确 R4 与 fallback 的诚实处理

### 需在执行前/中处理（P1）
- **P1-1 cann_compile 工具假设（U2 前置验证）**：`npu_tool.cann_compile` 调 `cann_compile` 二进制，但标准 CANN 编译流用 cmake/msoprun——该二进制可能不存在。**Spike 0 第一步**：在 A5/950 跑 `which cann_compile`，若不存在则 U2 先修 `npu_tool.cann_compile` 调真实 CANN 编译流（cmake+make 或 msoprun），再跑冒烟。"硬件门槛"实际是"工具+硬件"双重验证，需分开
- **P1-2 死代码删除影响 7 测试（U8 盘点 + re-export 方向反转）**：评审实测 workflow 死代码被 **7 个测试**引用（含 4 集成测试 test_e2e_local/test_self_improvement_loop/test_skill_flow 等），非 4 个。U8 执行前逐文件决定删除/重写。**re-export 方向反转**：models.py 留在 `workflow/` 原地，`orchestrator/models.py` 从那 import（而非计划原说的移走+反向 re-export）——避免 class identity/pickle/pydantic `__module__` 问题
- **P1-3 cannbot skill 内部步骤与 resume 粒度**：已并入 U6/U7（resume 粒度 = 编排器节点级，skill 内部 Step 0-7 不可单独恢复）
- **P1-4 时间线乐观**：P0 修正（节点执行契约细化）+ P1-1（cann_compile 可能要修）增加未预算工作。Phased Delivery 加 Week1 末决策门（见下），实际工期按 9-10 周预估

### Phased Delivery 调整（P1-4）
- **Week1 末决策门**：U2（cann_compile+硬件）必须通过才启动 Week2 foundation（U4/U5/U6）。U2 失败则 halt 重规划，不让 foundation 在失败硬件假设上推进
- **U6→U7 间 slack**：U6（垂直切片）是 P0-1/P0-2/P0-3 落地处，预留 0.5-1 周 slack；U7（backend 接线）待 U6 验证通过再启动
- 实际预估 9-10 周（原 7 周为乐观值），Week7 buffer 仍用于集成/硬件测试

### 评审认可的稳健决策（不改）
- 拒绝 LangGraph（顺序 DAG 甜区，依赖规避理由成立）
- workflow 死代码审计（3759 LOC 实证准确）
- 保留 models/compiler/performance，删 engine/phases/adapters/skill_save（方向正确，仅执行细节按 P1-2 调整）

---

## Sources & References

- **Origin document:** [docs/brainstorms/selective_migration_mvp.md](docs/brainstorms/selective_migration_mvp.md)
- **战略:** [docs/brainstorms/build_vs_buy_strategy.md](docs/brainstorms/build_vs_buy_strategy.md)
- **技术决策:** [docs/brainstorms/op_agent_refactor_decision_report.md](docs/brainstorms/op_agent_refactor_decision_report.md)
- **通用框架:** [docs/brainstorms/runtime_engine_internal_spec.md](docs/brainstorms/runtime_engine_internal_spec.md)
- cannbot-skills: https://gitcode.com/cann/cannbot-skills
- 项目 memory: positioning-pivot-to-runtime-engine
- Related code: `src/ascend_op_agent/agent/core.py`, `src/ascend_op_agent/agent/session_record.py`, `src/ascend_op_agent/backend.py`
