---
date: 2026-07-12
topic: skill-crystallization
---

## Summary

为 ascend_op_agent 增加 Skill Crystallization 机制：把项目所有任务类型（TaskType ∈ {migrate / analyze / optimize / develop}，对齐 [task_store/models.py](task_store/models.py) TASK_TYPES）过程中产生的可复用经验，自动或手动沉淀为自研 skill 库。入口是统一的 `skill_manage` agent 工具（替代当前散落的 `skills/storage.py` 直写 + `file_write` 临时拼装），通过 Layer 6 + 语义检索双路路由让 agent 自发现已沉淀经验，并通过**静态护栏（PR-A）+ 计划中的 LLM 护栏（PR-B/skill 数 >20 时启用）**保证 skill 库质量。落地分 L1（手动 `/learn`）/ PR-B（Layer 6 路由+语义检索+LLM self-check）/ V2=L3（后台 N 轮 review）三阶段，共享同一套写入与护栏栈。

## Problem Frame

STRATEGY.md 第 47-55 行的 "Skill Crystallization" track 已宣告方向（"将调试、性能优化经验自动固化为可复用 skill"），但当前架构缺落地能力：

- `skills/` 模块虽有 `SkillStorage` 的完整 save/load/delete 列表（[storage.py](skills/storage.py)），但**不在 agent 工具表中**（`agent/tools/` 下没有 skill 写入工具），也没接进 orchestrator 主流程，agent 无法触发沉淀。
- `orchestrator/cannbot_loader.py` 仅消费华为 cannbot-skills 作知识层，自研 skill 写入缺统一入口，会导致 skill 命名/分类/章节/与 cannbot 的边界全无约束。
- 当前 fix_loop 收敛时（[fix_loop.py:55](orchestrator/fix_loop.py#L55)）和 spike 验证后都没有 hook——踩坑经验全靠用户记忆手动写笔记，无法回流到 skill 库。
- 上一轮评估（2026-07-12）已确认要自研「算子经验 skill」，但范围严格限定在 cannbot 未覆盖的项目/硬件特定增量（910B3 + ops_pt + CANN 9.1.0），不自研通用迁移知识——护栏必须守住这条边界。
- **TaskRouter 与 Layer 6 现状（round 2 feasibility 揭示）：** `task_router/executor_dispatch.py` 仅 wired `develop` 类型，migrate/analyze/optimize 抛 `TaskGatedError`；`agent/prompt_builder.py:151-162` 的 Layer 6 是 static literal 文本，无 skills/ 目录扫描机制。R5a/R5b 的 Layer 6 路由假设需在 PR-A 内做 Layer 6 from-scratch 重写（详见 Outstanding Questions F-1）。

## Alternatives Considered

方向性决策须显式拒绝替代方案（adversarial review Q8 要求），避免"STRATEGY.md 宣告方向"被当成结论而非论证：

- **(a) `~/.ascend_op_agent/notes/` flat markdown + grep** —— 拒因：无 TaskType×Topic 结构化 scope，语义检索/路由退化成 grep 全文匹配；无 provenance/护栏；与 cannbot skill 形态不一致，Layer 6 无法统一渲染。适合个人笔记，不适合 agent 可路由的知识层。
- **(b) `.cursor/rules` 单文件 per session** —— 拒因：单文件装不下多 skill 的 Project Scope + 9 段章节结构；per session 语义跟"跨会话沉淀复用"目标相反；且 `.cursor/rules` 是 Cursor 专有，本项目 agent 不是 Cursor。
- **(c) 直接复用 cannbot skill 格式，不建 `self-built/` 目录** —— 拒因：cannbot 是 git submodule 只读知识层（R12 禁止 patch），自研 skill 必须写入可变目录；混存让 cannbot submodule 升级时冲突，且 provenance 无法区分来源。存储分离是 cannbot 只读 + 自研可写的必然结果。
- **(d) 纯 PR-review 人工审核取代（推迟的）LLM self-check** —— 部分采纳：Q6 决议本就把 LLM self-check 推迟到 skill 数 >20，初期靠静态校验 + 人工 review。但长期不依赖纯人工——skill 数增长后人工 review 不可持续，届时启用 LLM self-check（见 Key Decisions 护栏条）。

## Key Decisions

**统一的 `skill_manage` 工具入口** — 单一 agent 工具 + `action` 枚举（`create` / `patch` / `add_reference` / `archive` / `load` / `list` / `search`，共 7 个），所有写入与读取操作收敛到一个 schema。静态护栏（LLM self-check 推迟，见第 5 条）、provenance 标记、写入 hook 都集中在此工具内部，未来加 L3 自动时调用方代码零改动。代价：action 枚举一开始要设计全，规模可控。

**Skill 的 TaskType × Topic 两维 scope** — frontmatter `metadata.ascend_op_agent.task_type` ∈ {`migrate` / `analyze` / `optimize` / `develop`}（严格对齐 `task_store.TASK_TYPES`，零发明，task_router 已知当前 task.type，PR-B Layer 6 路由可零成本复用）+ `metadata.ascend_op_agent.topic` 按 TaskType 分桶：
- `develop` 下：`tiling` / `precision` / `build_env` / `kernel_pattern` / `dtype_handling`
- `migrate` 下：`cuda_frontend` / `triton_frontend` / `source_dialect` / `precision_alignment` / `delivery_format`
- `analyze` 下：`profiling_method` / `bottleneck_identify` / `metric_interpret` / `kernel_trace`
- `optimize` 下：`micro_mod` / `perf_strategy` / `tiling_opt` / `memory_opt`

**Topic 冻结策略（F-5 决议）：** PR-A 阶段 task_type ∈ TASK_TYPES **强校验**，但 **topic 是 free-form label，不参与 R5a 注入渲染**（仅供 R5b 路由与 R6 语义检索使用）。完整 Topic 枚举（上方 4 桶）作为**建议值**，不作为静态校验必填；R2 不校验 topic 是否在分桶枚举内。等首批 ~10 个 skill 落地且 intra-batch topic 冲突 > 0 时再冻结为必填（向前兼容，不破坏已沉淀 skill）。理由：4 task_type × 15 topic 槽位远超 PR-A 目标（5-10 skill），证据不足冻结。

两维索引避免自研 skill 与 cannbot 官方 skill 在 Layer 6 互踩，让语义检索可按 task_type + topic 双路过滤。命名规范继承 hermes 的 class-level 要求：skill 名是类别（`tiling_pitfalls`），不含具体任务对象（避免会话产物）。与 cannbot 边界：**`migrate` 且 topic ∈ {`cuda_frontend`, `triton_frontend`} 优先走 cannbot；其余 TaskType × Topic 自研优先**。

**Layer 6 注入 + 语义检索双路路由** — PromptBuilder Layer 6 渲染当前 `task.type`（来自 task_router）命中 + 同 `task_type` 命中 + 最近 5 轮被 `skill_manage(action="load")` 加载过的 skill `name`+`description` 列表（不注入 body，LLM 按名用 `skill_manage(action="load")` 读详情）；不在 Layer 6 列表内的 skill 走 `skill_manage(action="search")` + ChromaDB 语义检索。渲染格式同 cannbot：`- **<name>** (task_type=<tt>/topic=<t>): <description 首行 ≤60 字符>`。

**L1 + L3 共享护栏与写入栈** — 沉淀时机只支持两类：`/learn` 手动触发（L1，2-3 天可上线）和后台每 N turn 起的 review fork 跑 skill 审核与沉淀（L3，2-3 周大重构）。**不做** fix_loop 收敛时 hook 和 spike 验证后 hook（这两个时机在 L3 之后评估，避免 hook 点爆炸）。**L3 启动客观 gating（F-2 决议）：** 满足全部 3 条才可启动——(a) ≥8 skills 通过 R2；(b) 4 周内 ≤2 patch 事件触及 topic enum 冲突；(c) 0 R2 held-out regression。任一未达 = 推迟 L3，继续 PR-B 路由+语义检索巩固。

**静态护栏为主，LLM self-check 推迟（Q6 决议）** — PR-A 写入前只做静态校验（必填字段、name class-level、不与 cannbot skill 同名、TaskType+Topic scope 元数据齐全、body 首段 Project Scope），不通过返回结构化错误给 agent。LLM self-check（去 hermes 负面清单：环境依赖失败 / "X 工具坏了"断言 / 一次性任务叙述）**推迟到 PR-B 或 skill 数 >20 时启用**——内部工具定位 + GLM rate-limit 脆弱性（CLAUDE.md 已记录连续调用 timeout）下，初期 skill 少时静态校验 + 人工 review 够用，避免 LLM 调用进写入热路径引入 fail-open/fail-closed 两难。启用 LLM self-check 时须先解决超时处置（默认 fail-closed + retry-once 切备用 provider）与 provider 配额争抢。L3 阶段不递归调 self-check，改用 _SKILL_REVIEW_PROMPT 同款硬约束 prompt 内嵌到 review fork 的工具调用约束。

## Actors

A1. **算子开发者**（人）—— GPU 工程师迁移算子到 AscendC 的过程中，使用 `/learn` 命令描述来源（目录 / URL / "刚才我们做的" / 粘贴的笔记），或修正 agent 沉淀的 skill。

A2. **前台 agent**（`AIAgent` 主循环）—— 多轮工具调用循环中，按 task_router 路由 + PhaseRunner 编排执行各 task_type（migrate / analyze / optimize / develop）的节点；可被 `/learn` 触发一次蒸馏流程（构造 prompt + 塞输入队列）；也可在后端回调里接收 L3 review fork 的"建议沉淀"通知（仅展示，不自动应用）。

A3. **后台 review fork**（`AIAgent` 子实例，L3 引入）—— 每 N 轮在前台 agent 之外 fork 一个独立 agent 实例，重放对话历史，使用同一套 `skill_manage` 工具写入 skill；父 agent 不感知 fork 的存在，父子不共享 prompt cache（因为是不同 model key），但共享 `_cached_system_prompt` 同模型时。

A4. **SkillCrystallizer**（V2 reserved，L3 阶段引入）—— L1/PR-A 不实现，仅在此预留。L3 落地时承担：控制 review fork 触发节奏（每 N 轮或 fix_loop 收敛后）、把 review fork 写入结果经 A2 回调给用户展示、把 review fork 的 `skill_manage` 调用写入 provenance（`write_origin="background_review"`）。L1 范围内不出现于任何 flow 的 Actors。

## Requirements

### Skill 写入入口与护栏

- R1. `skill_manage` agent 工具支持 7 个 action：`create` / `patch` / `add_reference` / `archive` / `load` / `list` / `search`；每个 action 有独立 schema，schema 在 `agent/tools/` 下新增 `skill_manage_tool.py` 实现。
- R2. 工具内部在 `create` / `patch` 写入前先做静态校验：必填字段（name / description / task_type / topic / body）、name 必须小写连字符且 class-level（不允许含 PR 号、错误字符串、日期、具体任务对象）、task_type ∈ TASK_TYPES、topic ∈ 该 task_type 的分桶枚举、不与 cannbot skill 同名、`body` 第一段必须是 `## Project Scope` 章节（仅自研 skill 强制）；校验失败返回结构化错误给 agent。**这是 PR-A 的唯一护栏**（Q6 决议）。
- R3. (PR-B / skill 数 >20 时启用) 静态通过后调一次 LLM self-check（复用 [memory/llm_enhancer.py](memory/llm_enhancer.py) 的 LLMClient 调用模式），prompt 内嵌 hermes `_SKILL_REVIEW_PROMPT` 的负面清单：拒绝捕获环境依赖失败（缺二进制/未配置 credential）、"X 工具坏了"类断言、一次性任务叙述。self-check 必须显式决定超时/失败的处置（默认 fail-closed + retry-once 切备用 provider），避免与主 agent 调用争抢同一 provider 配额。**PR-A 不实现 R3**——初期靠 R2 静态校验 + 人工 review（Q6）。
- R4. 所有写入自动写入 frontmatter `metadata.ascend_op_agent.provenance`：`write_origin`（PR-A 仅取 `manual`；V2 扩展 `background_review` / `review_followup`）+ `created_at` / `updated_at` + `session_id` + `parent_session_id`；为 L3 阶段的审计与回滚提供数据基础。

### Skill 检索与路由

- R5a (PR-A). PromptBuilder Layer 6 全量注入 `~/.ascend_op_agent/skills/self-built/` 下所有 SKILL.md 的 `name`+`description`（**不分组、不按 task_type 路由、不做语义检索**），让 agent 能看到自己写的 skill，PR-A 闭环可观察（Q5 决议）。渲染格式：`- **<name>** (task_type=<tt>/topic=<t>): <description 首行 ≤60 字符>`。**内置降级阈值（F-4 决议）：** 当 self-built skill 数 > 20 时，R5a 自动从"全量注入"降级为"（最近被 `skill_manage(action="load")` 加载过的 + 同 `task_type`）子集注入"，避免 Layer 6 prompt budget 撑爆；待 PR-B R5b 路由上线后取消此降级。
- R5b (PR-B). Layer 6 路由收敛为"路由命中"：与当前 `task.type`（来自 task_router）命中的 + 同 `task_type` 的 + 最近 5 轮被 `skill_manage(action="load")` 加载过的，替代 R5a 的全量注入。
- R6. (PR-B) 不在 Layer 6 列表内的 skill 通过 `skill_manage(action="search")` 走 ChromaDB 语义检索，索引字段为 `name + description + task_type + topic + tags`。`semantic_memory.py` 已有的 SkillIndex 复用，不新建索引。
- R7. 自研 skill 与 cannbot skill 在 Layer 6 共存但分组渲染（**cannbot 在前**——一般迁移知识 prior 更高；自研在后——910B3/ops_pt/CANN 9.1.0 特定增量），分组标题明示来源（"Cannbot Skills" / "Self-built Skills"）。

### 沉淀时机

- R8. L1 阶段：实现 `/learn` CLI 命令（参考 hermes 的 `_handle_learn_command` 设计）：用户描述来源（自由文本），构造 `build_learn_prompt()` 注入 agent 输入队列，agent 通过 `skill_manage(action="create")` 写入。`build_learn_prompt()` 内嵌昇腾算子语境的 `_AUTHORING_STANDARDS`：description ≤60 字符（Layer 6 截断）、body 章节固定 9 段——首段 `## Project Scope`（适用范围 + 版本边界），后续 8 段（When to Use / Prerequisites / How to Run / Quick Reference / Procedure / Pitfalls / Verification / Related Skills）、强制 task_type+topic 元数据、禁止重复 cannbot 已覆盖的通用知识。
- R9. L3 阶段（V2，不在本期范围）：实现后台 review fork，每 N 轮（默认 10）fork `AIAgent` 子实例跑 `_SKILL_REVIEW_PROMPT`，按偏好顺序 UPDATE 已有 skill → ADD reference/template/script → CREATE 新 skill；fork 走 `skill_manage` 工具但必须命中 LLM self-check，不递归调用 self-check 而是在 review fork 的 system prompt 内嵌硬约束。

### Skill 形态与边界

- R10. 自研 skill 必须有 frontmatter `metadata.ascend_op_agent.task_type`（∈ TASK_TYPES）+ `topic`（按 task_type 分桶，见 Key Decisions 枚举）；cannbot skill 不需要这层（cannbot 自带 description 触发词）。两者存储路径：自研由 `skill_manage` 写入 `~/.ascend_op_agent/skills/self-built/`，cannbot 由 cannbot_loader 直读 git submodule `vendor/cannbot-skills/`（**不落盘**）；Layer 6 与语义检索渲染时跨两源 join，因此 cannbot_loader 无需改路径。
- R11. 自研 skill 的 body 第一段必须是 "## Project Scope" 明示适用范围（例："适用于 910B3 + ops_pt 容器 + CANN 9.1.0，CANN 9.2+ 需重新验证"），防止过期 skill 被误用。
- R12. 自研 skill 不允许覆盖或 patch cannbot 官方 skill：写入时静态校验 name 不与 cannbot skill 冲突，patch 时校验目标 skill 的 provenance.cannbot_origin != true。

### 存储与持久化

- R13. `skill_manage` 写入走 `skills/storage.py:SkillStorage.save_skill`，复用现有三档 dimension 后缀（template / bugfix / performance）；新增 `dimension="reference"` 是 storage 层的一处小扩展（在 save_skill 的 dimension 分支加一档），不属"零成本复用"，归入 PR-A 范围。底层文件结构不变。
- R14. 写入成功后触发 `SkillIndex.rebuild_index`（参考 `skills/installer.py:81` 的现有逻辑），保证 Layer 6 与语义检索能立刻看到新 skill；rebuild 必须是增量更新或 turn 级批量（在一次 `/learn` 流程的多 skill 写入合并为单次索引更新），不得在写入热路径做全量同步 rebuild。

### 错误与回滚

- R15. `skill_manage(action="archive")` 把 skill 移至 `~/.ascend_op_agent/skills/.archived/`，不在 Layer 6 与语义检索出现但保留文件供回滚；archive 写入 provenance `archived_at` 与 `archive_reason`。
- R16. 静态校验与 LLM self-check 失败时返回结构化错误（含具体失败原因与修复建议），让 agent 能自我修复而非直接放弃。

## Key Flows

### F1. 用户手动沉淀（`/learn`）

- **Trigger:** A1 在 CLI 键入 `/learn <自由文本描述来源>`。
- **Actors:** A1, A2, SkillStorage
- **Steps:**
  1. `_handle_learn_command` 解析文本，调用 `build_learn_prompt(user_request)` 构造完整 instruction（内置 `_AUTHORING_STANDARDS`）。
  2. prompt 注入 A2 的 `_pending_input` 队列作为普通一轮对话。
  3. A2 用 `file_read`/`file_search`/`web_extract` 等已有工具收集素材（按 user_request 描述的来源）。
  4. A2 决定写新 skill 还是 patch 已有 skill，调 `skill_manage(action="create"|"patch")` 携带完整 content（含 task_type + topic 元数据）。
  5. `skill_manage` 工具内部：R2 静态校验 →（PR-A 不做 LLM self-check，Q6）→ `SkillStorage.save_skill` → `SkillIndex.rebuild_index`。
  6. 静态校验失败返回结构化错误（R16），A2 看到后自我修复重试（hermes 同款"agent self-repair"循环）。
- **Covers:** R1, R2, R4, R8, R13, R14, R16
- **Outcome:** 用户收到 agent 回复："已沉淀 skill '<name>'，task_type=<tt> topic=<t>，捕获了 <一句话>"

### F2. Agent 自检索已有 skill (PR-B)

- **Trigger:** A2 进入任一 PhaseRunner 节点，PromptBuilder 构造 prompt。
- **Actors:** A2, PromptBuilder, SkillIndex, SkillStorage
- **Steps:**
  1. (PR-A 过渡期) PromptBuilder Layer 6 全量注入 `self-built/` 下所有 SKILL.md 的 name+description（R5a glob）。
  2. (PR-B 终态) Layer 6 路由收敛为"路由命中"：当前 `task.type`（来自 task_router）命中 + 同 `task_type` 命中 + 最近 5 轮 `skill_manage(action="load")` 调用过的。
  3. 渲染为 `- **<name>** (task_type=<tt>/topic=<t>): <description 首行 ≤60 字符>` 列表，cannbot 在前（一般迁移知识 prior 更高），自研在后。
  4. A2 在 prompt 里看到 skill 列表后按需调 `skill_manage(action="load")` 读详情（body）。
  5. 不在 Layer 6 列表内的 skill，A2 调 `skill_manage(action="search", query=...)` 走语义检索。
- **Covers (PR-A):** R5a, R10 / **(PR-B):** R5b, R6, R7
- **Outcome:** A2 在新算子开发时能自发现"上次在 910B3 + Triton translate 上踩过的 tiling 坑"，prompt 自动包含该 skill 列表

### F3. Skill 写入护栏链路

- **Trigger:** 任何 `skill_manage(action="create"|"patch")` 调用。
- **Actors:** SkillStorage, SkillIndex（PR-A）；启用 R3 后加 LLMClient
- **Steps:**
  1. R2 静态校验：必填字段、name class-level、不与 cannbot skill 冲突、task_type+topic scope 元数据齐全、`body` 第一段为 `## Project Scope`。
  2. (PR-A 跳过) LLM self-check：把 skill draft + 负面清单 prompt 发给 LLMClient，返回 `pass: bool, reason: str`。超时/失败按 R3 处置策略（默认 fail-closed + retry-once 切备用 provider）。
  3. 任一步骤失败返回错误给调用方 agent（结构化含修复建议，R16）。
  4. 全通过则写 `SkillStorage.save_skill` + 触发 `SkillIndex.rebuild_index`（增量或 turn 级批量，详见 R14）。
  5. 写入 frontmatter provenance 元数据（write_origin=manual / created_at / updated_at（patch 时必写）/ session_id / parent_session_id（如有）），完整字段见 R4。
- **Covers:** R1, R2, R4, R10, R11, R12, R13, R14, R16（启用 R3 后加 R3）
- **Outcome:** skill 库只接受形态合规、与 cannbot 不重复、捕获可持续复用经验的 skill

## Acceptance Examples

- AE1 (PR-A). 用户在 CLI 输入 `/learn ops_pt 容器里 build AscendC 算子的 build.sh 配置` → agent 用 `file_read` 读 ops_pt 容器内历史 build.sh 样例 → 调 `skill_manage(action="create")` 写一个新 skill（task_type=develop / topic=build_env）→ **期望：** 写入成功后 agent 回复 skill name、task_type、topic、一句话摘要，且 Layer 6 下次渲染时该 skill 出现在自研 skill 全量列表（R5a）。
- AE2 (PR-B，依赖 R3 LLM self-check). 用户输入 `/learn CANN 9.1.0 的 set_env.sh 在 ssh 调用时不生效` → agent 尝试写 skill → **期望：** LLM self-check 因"环境依赖失败"命中 hermes 负面清单拒绝写入，返回 `Blocked: captures environment-dependent failure, not a reusable rule`；agent 据此不写并向用户解释。**PR-A 阶段此例不被拦截**（无 LLM self-check），靠人工 review 兜底——这是 Q6 折中的已知代价。
- AE3 (PR-A 部分 / PR-B 完整). Agent 在 develop 任务中遇到 `910B3 tiling 踩坑` → **期望：** Layer 6 自动注入 `tiling_pitfalls`（如果已沉淀，PR-A 全量注入即命中），agent 读完后能直接应用经验；如果没沉淀过，agent 通过 `skill_manage(action="search")` 语义检索（PR-B）能命中语义相近的 skill（即便名称不完全匹配）。
- AE4. 用户调用 `skill_manage(action="patch")` 修改 cannbot 官方 skill `ascendc-tiling-design` → **期望：** 静态校验失败返回 `Refusing patch on cannbot-owned skill: ascendc-tiling-design`，写入被拒。
- AE5a (PR-A). 用户输入 `/learn`（无参数）→ **期望：** 返回教学性错误，提示用户补来源描述（目录/URL/"刚才我们做的"/笔记），不进入蒸馏流程。
- AE5b (PR-B/V2). `/learn` 无参数触发会话历史 replay 蒸馏（hermes 同款"review what we just did"），先 `skill_manage(action="list")` 避免重复再决定 create/patch。

## Success Criteria

**PR-A 验收标准（可证伪、本期闭环可测）：**

- **Schema 合规率：** PR-A 写出的 skill 100% 通过 R2 静态校验；任何静态校验失败必须返回结构化错误（R16）。
- **静态护栏召回：** 在 held-out 的 5 条明显违规案例（name 含日期/PR 号、缺 TaskType 元数据、与 cannbot skill 同名、body 无 Project Scope 段）上，R2 静态校验全部拒绝，0 漏放。注：环境依赖失败/一次性任务这类语义级违规静态校验拦不住，靠人工 review 兜底，待 R3 LLM self-check 启用后补测。
- **Layer 6 注入可观测：** 写入 self-built/ 的 skill 在下一轮 prompt 的 Layer 6（R5a 全量注入）可见，agent 能 `skill_manage(action="load")` 读到 body。
- **可演进：** L3（V2）启动时不需改 `skill_manage` 工具接口——新增 review fork 只需在 SkillCrystallizer 里调同一工具，护栏与 provenance 自动生效。

**产品级 metric（**F-3 决议：降级为观测性指标**，非硬门槛，PR-A 上线后人工 review 决定是否推进 PR-B；候选池 Q7 决议：复用现有 spike/e2e 任务）：**

- **沉淀量：** L1 上线 4 周内 `~/.ascend_op_agent/skills/self-built/` 沉淀的形态合规 skill 数量。**观测性记录**，不设阈值。
- **自发现率：** 跑跨 task_type 的评估任务池——`develop`: vector_add（e2e_real_op.py 已验证）+ 1 个 micro_mod 优化算子；`migrate`: 1 个 cuda/triton 算子迁移（cuda2ascend-simt 覆盖范围）；`analyze`: 1 个 profiling 任务（npu-smi/msop）；`optimize`: 1 个 micro_mod 性能任务——统计 agent 在 prompt 看到 skill 列表后主动调 `skill_manage(action="load")` 的比例。**观测性记录**，不设阈值。

**静态护栏 false-negative 基线（F-6 决议，PR-A 验收可证伪）：**

- 在 held-out 的 5 条**语义违规案例**（AE2 的 env-dep 模式 + 一次性任务 + "X 工具坏了"类断言等，AE2 是其中之一）上跑 R2 静态校验，量化 false-negative 率。**预期 ≥60%**（R2 本就拦不住语义违规，这是 Q6 折中的已知代价）。
- **PR-B R3 LLM self-check 启用后**，用同一 held-out 集复测，delta 是 R3 的真实价值（≥60% → ≤10% 是 R3 启用的硬指标，否则不进 R3）。

## Scope Boundaries

### Deferred for later（V2 评估后再做）

- **L3 后台 review fork**：每 N 轮自动 fork AIAgent 跑 skill 审核与沉淀（依赖本期 R1-R8 的工具与护栏基础设施）。
- **fix_loop 收敛时 hook**：在 `run_fix_loop` 返回 status="done" 时自动触发 review fork，把本次 review_issues + 修复产物作为候选沉淀输入（依赖 L3 先稳定）。
- **spike 验证后 hook**：`spike_kernel_feasibility.py` / `e2e_real_op.py` 验证成功后插入 skill_write hook（依赖 L3 先稳定）。

### Outside this product's identity（明确不做）

- **自研通用迁移知识**（cuda2ascend-simt、triton-op-coding 等通用模式）——canbot 已覆盖，自研浪费且与 cannbot 重复；护栏 R12 强制拦截。
- **覆盖或修改 cannbot 官方 skill**——cannbot 由 git submodule 管理，自研只能"另起新 skill"，不能就地 patch；护栏 R12 强制拦截。
- **skill 库云端同步 / 团队共享**——项目内部工具，不商业化（[positioning-pivot-to-runtime-engine](memory/positioning-pivot-to-runtime-engine.md) 2026-06-23 战略评估结论）；skill 库仅本地。
- **skill 版本号管理与升级提示**——L1 阶段 skill 形态还没稳定，过早上版本管理会让迭代变重；V2 评估时再加。

## Dependencies / Assumptions

- **Assumption:** 项目当前定位（选择性批量迁移引擎 + cannbot 作通用知识层 + 自研 cannbot 未覆盖的算子经验 skill）仍然成立（[positioning-pivot-to-runtime-engine](memory/positioning-pivot-to-runtime-engine.md) 2026-07-12 已确认）。若定位再次变化，本机制需重新评估范围。
- **Dependency:** `skills/storage.py` 的 `SkillStorage.save_skill` 已具备完整三档 dimension 后缀能力，本期直接复用，不重构。
- **Dependency:** `orchestrator/cannbot_loader.py` 的 SKILL_BUNDLES 决策表与 frontmatter 解析，本期不修改。cannbot skill 仍由 cannbot_loader 从 `vendor/cannbot-skills/`（git submodule）直接读取，**不落盘到 `~/.ascend_op_agent/skills/cannbot/`**；R10 的存储目录拆分只作用于 `skill_manage` 的自研写入侧（`~/.ascend_op_agent/skills/self-built/`）。Layer 6 与语义检索在渲染时跨两个根（vendor cannbot + self-built）join，因此 cannbot_loader 无需改路径。
- **Dependency:** `memory/llm_enhancer.py` 的 LLMClient 调用模式可直接复用做 self-check，本期不修改。
- **Dependency:** `prompt_builder.py` Layer 6 已有 `skills_layer_override` 注入点，本期新增"路由命中 skill 列表"在 Layer 6 默认渲染逻辑里，与 cannbot 注入并列。
- **Assumption:** ChromaDB 已在 `~/.ascend_op_agent/` 部署（[memory/vector_store.py](src/ascend_op_agent/memory/vector_store.py)），语义检索可零成本启用。**降级路径：** `SkillIndex` 的向量层依赖 SentenceTransformer embedding 模型懒加载，模型不可用时（如离线 910B 环境）自动降级为 SQLite FTS5 全文检索——功能不崩，但 AE3 的"语义近似命中"在该情形下不保证，FTS5 schema 也无 project/topic 列（按 project+topic 双路过滤需扩 schema 或走向量 metadata），属 PR-B planning 需拍板的实现细节。

## Outstanding Questions

- **Resolve Before Planning:**（全部已解决）
  - Q1. `skill_manage` 工具实现分 2 期 PR——**已决议（Q5/Q6 更新后）**：
    - **PR-A（本期必做，~3-4 周，F-1 决议后扩）：** `skill_manage` agent 工具（7 action schema）+ **R2 静态校验（唯一护栏，无 LLM self-check）** + `SkillStorage` 复用 + `dimension="reference"` 扩展 + L1 `/learn` CLI 命令 + **底层修复（4 task_type 可达性的前置）**：(a) `task_router/executor_dispatch.py` 拆 migrate/analyze/optimize 的 gated，至少加 stub executor（pass-through 或外部占位），使 3 类 task_type 也能跑过 PhaseRunner 节点、产生经验可结晶；(b) `agent/prompt_builder.py` Layer 6 from-scratch 重写（含 `~/.ascend_op_agent/skills/self-built/` + `vendor/cannbot-skills/` 扫描 + cannbot 优先 + 自研在后的渲染），作为 R5a glob 的基础设施；(c) 决定 `(graph, phase) ↔ (task_type, topic)` 映射表（cannbot_loader 的 SKILL_BUNDLES 键空间与新 topic 枚举对齐），并由 PhaseRunner 在调度时填 `task_type` 上下文（orchestrator/nodes/common.py 的 `run_conversation` 调用点加 `task_type` 参数）。
    - **PR-B：** R5b Layer 6 task_type 路由收敛（从 R5a 全量注入进化为路由命中）+ R7 自研/cannbot 分组渲染（R5a 阶段底层重写已部分支持）+ R6 `skill_manage(action="search")` 语义检索 + R3 LLM self-check（启用条件：skill 数 >20 或人工 review 吃不消）。依赖 PR-A 落地。
  - Q5. **PR-A scope 边界** —— **已决议：并入 Layer 6 最小 glob（R5a）**。PR-A 不再做"写得到但用不上"的孤岛；agent 在 PR-A 即可看到自研 skill，自发现率可在 PR-A 测。
  - Q6. **80/20 premise 替代** —— **已决议：折中**。保留 skill_manage 工具 + R2 静态校验（hermes 式机制骨架，零 LLM 依赖），推迟 R3 LLM self-check 到 PR-B/skill>20。避开 GLM rate-limit 脆弱性与 fail-open/fail-closed 两难，PR-A 从 2-3 周降到 ~1-1.5 周。
  - Q7. **产品级 metric 候选池** —— **已决议：复用现有 spike/e2e 任务**。自发现率评估池跨 4 个 task_type（vector_add develop / cuda/triton migrate / profiling analyze / micro_mod optimize），零新造成本，候选池可追溯。沉淀量/自发现率目标值上线 4 周后据实测定，不预设硬门槛。
  - Q8. **Alternatives Considered** —— **已决议：已补章节**（Problem Frame 后），显式拒绝 flat notes / .cursor rules / cannbot 格式混存 / 纯人工审核 4 个替代，各附拒因。
  - Q9. **命名规范** —— **已决议：name 仅 topic**（`tiling_pitfalls`），task_type 只留 metadata，让 skill 跨 task_type 复用；命名稳定不绑项目对象（避免 910B3→910B4 改名风暴）。文档示例已从 `ascend910b3_tiling_pitfalls` 改为 `tiling_pitfalls`。
- **Deferred to Planning:**
  - Q2. Layer 6 "路由命中"skill 列表的最大长度上限是多少（R5b PR-B）？hermes 默认注入 ~10 个，本项目是否需要按 task_type 不同调整？让 ce-plan 阶段定。
  - Q3. R3 LLM self-check 的 prompt 模板是写在 `skill_manage_tool.py` 里独立维护，还是抽出到 `agent/skill_standards.py` 与 `/learn` 的 `_AUTHORING_STANDARDS` 共用？PR-B 启用 R3 时定。
  - Q4. L3 review fork 的 N 轮节奏（默认 10）是配置项还是写死？以及 fork 是否要支持用户在 profile 里 disable？V2 阶段定。