# 算子 Agent 是否值得存在——战略级评估（买 vs 造）

> **用途**：回答"现有 coding agent + 昇腾官方 skills 够不够、必须自研算子 agent 的理由、能否说服投资/开发团队"。
>
> **证据基础**：3 路并行调研
> 1. cannbot-skills 仓库内容解剖（52 skills + 9 编排插件 + 3 层 agent 架构）
> 2. 华为官方 AI 生态调研（cannbot 产品、MindStudio、华为云、竞争格局）
> 3. 纯 skills 方案天花板分析（8 缺口逐项裁决）
>
> **日期**：2026-06-22

---

## 执行摘要

**核心判断：原项目"算子开发 agent"的定位已被 cannbot-skills 大幅削弱，但"运行时引擎"这个重新定位是真实、可防守、且华为结构性不会做的。这不是"要不要做"的问题，而是"定位要不要从 agent 转向 runtime"的问题。**

三个问题速答：

| 问题 | 结论 |
|---|---|
| 现有 coding agent + cannbot-skills 够不够？ | **单算子够（60-70%），团队/批量/企业不够（30-40%）** |
| 必须自研算子 agent 的理由？ | **不是"agent"，是"运行时引擎"**——只有这个角度站得住 |
| 能说服投资/开发团队吗？ | **能，前提是重构论据**：放弃"领域知识/算子 agent"卖点，押注"华为留白的运行时层" |

---

## 第一部分：现有方案够不够

### 1.1 cannbot-skills 的真实能力（颠覆性发现）

华为 CANN 团队官方维护的 `gitcode.com/cann/cannbot-skills`（1k+ stars，每周多次合并，OAT 开源）远比预期成熟：

- **52 个算子开发 skills**（`ops/`）：AscendC API、tiling 设计、runtime/crash debug、UT/ST 开发、白盒测试、profiling、code-review、spec-gen
- **12 个推理优化 skills**（`model/`）：KVCache、fusion、quantization、graph mode、superkernel 等
- **9 个编排插件**（`plugins-official/`）：每个含 `AGENTS.md` + 3 个 specialist agent（architect/developer/reviewer）+ skills/ + workflows/ + hooks/
- **全生命周期覆盖**：spec → kernel → tiling → build 模板 → UT/ST → precision → profiling → review → delivery
- **格式**：SKILL.md + YAML frontmatter，与 Claude Code / Cursor / Trae / Hermes / VS Code Copilot **完全兼容**
- **质量信号**：脚本可运行（如 `msprof_profile_run.sh`）、约束求解器（`ascendc-st-design` 的 6 个 Python 脚本做拓扑排序）、硬件版本区分（DAV_2201 vs DAV_3510）、CANN 版本兼容性标注

**这意味着**：昇腾算子开发的"知识层"已经被华为商品化，且质量是任何第三方难以匹敌的（他们是源、是权威、有数据集）。

### 1.2 纯 skills 方案的覆盖率天花板

| 场景 | Claude Code + cannbot-skills 覆盖率 |
|---|---|
| 单个算子全生命周期（熟练开发者当 pair programmer）| **60-70%** |
| 团队级算子交付（批量/CI/审计/NPU 调度）| **30-40%** |

单算子各阶段细分：

| 阶段 | 覆盖率 | 原因 |
|---|---|---|
| Spec 生成 | 90% | 知识合成，skills 甜区 |
| Kernel 脚手架 | 80% | 模板 + API 指引，skills 擅长 |
| Tiling 逻辑 | 65% | 需推理，LLM 部分出错 |
| Build/编译 | 50% | 命令是知识，运行时/设备/环境是 runtime |
| UT | 75% | 模板是知识，运行是 runtime |
| ST/精度 | 45% | 基线对比、数值容差 = 代码 |
| Profiling | 40% | 批量 benchmark、基线 diff = 编排 |
| Code review 闭环 | 60% | 聊天能做，规模化脆弱 |
| 交付/审计 | 20% | 无结构化记录 |

**结论**：作为开发者工具的天花板是 60-70%；作为产品（无人值守交付）的天花板是 30-40%。差的 30-40% 全是运行时——这正是产品价值。

---

## 第二部分：必须造的理由——运行时层

### 2.1 轴心判别标准

> 如果把某个组件删掉，换成系统提示里一段说明文字，95% 场景行为不变——它是**知识**（skill）。如果删掉后**正确性保证消失**（可能静默跳步、丢状态、死锁、产出不可审计）——它是**编排**（代码）。

按此标准：

| cannbot-skills 提供的 | 类别 |
|---|---|
| 52 个 SKILL.md（API/tiling/debug/UT/ST/profiling/review）| ✅ 知识 |
| 9 个 plugins-official 的 AGENTS.md + 3 个 persona agent | ⚠️ 编排配方（散文描述编排，仍靠宿主遵守）|
| architect/developer/reviewer 三角色 | ⚠️ persona prompt，**不是进程** |

**关键**：华为的 `plugins-official/ops-direct-invoke` 定义了 architect→developer→reviewer 流水线，但**它本身不含运行时**——是一份"可移植编排菜谱"，设计成被任何宿主加载执行。它故意不含宿主，因为华为要让所有宿主都能用（最大化 skills 覆盖面），这**恰恰把运行时层留给了生态**。

### 2.2 八缺口逐项裁决

| 缺口 | 纯 skills 能否解决 | 必须造代码 | LOC |
|---|---|---|---|
| **崩溃后从 Phase 4 恢复** | ❌ skills 无状态；Claude Code 的 JSONL 是对话历史不是类型化阶段机 | ✅ 持久化阶段机 + checkpoint | ~600-900 |
| **闭环修复循环**（review→fix→re-review→stop）| ⚠️ 单算子聊着能做 60-75%，但无确定性停止条件、上下文漂移、可能死循环 | ✅ 有界修复控制器 | ~300-500 |
| **跨 skill 数据契约** | ⚠️ 靠路径约定，单算子够用，批量/并行时路径漂移、部分输出、文件覆盖 | ✅ 制品注册表 + 契约校验 | ~250-400 |
| **NPU 硬件调度** | ❌ skills 无法持设备锁、排队、检测 OOM/ECC/hang、归档结果、对比基线 | ✅ NPU 调度器（最大一块）| ~1200-2000 |
| **GPU→AscendC 深度迁移**（cuda2ascend-simt 是空桩）| ❌ SMEM↔UB、tile 推断、异步流水线映射是**算法变换**不是知识 | ✅ 规则翻译器（部分超范围）| ~2000-4000/类 |
| **确定性审计**（企业交付）| ❌ 对话 transcript 不是审计记录，不可签名、不可查询 | ✅ 结构化审计日志 | ~400-700 |
| **CI/CD 自动触发** | ❌ CI 跑代码不跑 prompt | ✅ CI 驱动器 | ~500-900 |
| **批量迁移 200 个算子** | ❌ 通用 agent 是 1 个/会话/顺序 | ✅ 批量作业控制器 | ~800-1400 |

### 2.3 不可缩减的最小层

**缺口 1 + 4 + 6 + 8 = ~3000-5000 LOC**——这 4 项"只能用代码、skills 永远填不上"：

1. 持久化阶段机（崩溃恢复）
2. NPU 调度器（设备锁/排队/基线对比）
3. 结构化审计（企业交付）
4. 批量作业控制器（200 算子并行）

其余 4 项（修复循环、数据契约、深度迁移、CI/CD）在之上叠加。

---

## 第三部分：华为的生态布局与威胁评估

### 3.1 华为在做什么、不做什么

| 华为的产出 | 性质 | 是否构成威胁 |
|---|---|---|
| **cannbot-skills**（52 skills + 9 编排插件）| 知识 + 编排配方，骑在第三方 agent 上 | 知识层碾压第三方；**运行时层留白** |
| **MindStudio** | 传统 IDE（无 LLM/AI 代码生成）| 无威胁——传统工具链 |
| **AKG / AOE** | 规则/编译器的自动调优（非 LLM）| 不在同一赛道 |
| **ModelArts** | 托管 CANN 环境，不生成算子 | 无威胁 |
| **CodeArts 码道**（2026-02 公测）| 通用编码 agent（类 Cursor）| **潜在威胁**——若华为出"算子版"，但当前无算子专精 |
| **KADC 2026 算子数据集 + eval set**（2026-05）| 赋能第三方 LLM/agent 适配昇腾 | 明确信号：华为**做生态不做 agent 运行时** |

**关键发现**：华为 anointed **Claude Code** 作为 cannbot-skills 的参考运行时（官方 install 路径是 `/plugin marketplace add`）。这等于公开声明：agent 运行时层是"别人的问题"，华为只做知识层。

### 3.2 竞争格局（2025-2026）

- **华为官方**：cannbot-skills（知识层）+ KADC 数据集（赋能层）
- **中国 AI 编码厂商**（Trae/通义/文心/智谱/月之暗面/DeepSeek）：**均无**昇腾算子专精 agent，只有通用编码 agent
- **学术/草根**：KernelGen v1.0（Triton 算子生成，昇腾感知但非专精）
- **第三方昇腾算子 agent**：**市场基本空白**

**结论**：昇腾算子 agent 的生态位 = 华为的 skills + 少数研究项目。没有主流厂商在做"算子开发的运行时引擎"。

### 3.3 威胁裁决：是机会还是死局

**不是死局，是窗口期。** 理由：

- 华为不做运行时是**战略选择**（最大化 skills 覆盖面），不是能力不足
- 没有任何主流编码厂商做昇腾算子专精
- "运行时层"是华为结构性留给生态的空白

**但窗口在收窄**：KADC 2026 数据集 + eval set 的发布，会吸引更多第三方进入。现在做能抢占"运行时层"心智。

---

## 第四部分：对投资团队的论据

### 4.1 ❌ 不要用的卖点（已被吸收，会被碾压）

1. ~~"我们懂昇腾算子开发领域知识"~~ → cannbot-skills 有 52 个 skill，华为官方维护，比任何第三方更权威
2. ~~"我们做算子开发 agent"~~ → 华为 anointed Claude Code 作为运行时，你在跟华为选定的栈竞争
3. ~~"我们封装 AscendC API/tiling/debug"~~ → 这是 skill 内容，华为写得更好更快（每周多次合并）

### 4.2 ✅ 站得住的卖点（华为明确不做）

1. **运行时，不是知识**：cannbot-skills 是华为"故意留白的编排层"。我们做**把他们的编排配方变成确定性引擎**的宿主——消费他们的 skills，不重写。

2. **硬件在环（hardware-in-the-loop）**：NPU 调度、多租户设备共享、结果基线对比、批量 profiling——这 1200-2000 LOC 的调度器是任何 prompt 级方案的结构性盲区。昇腾 NPU 是稀缺物理资源，谁能高效调度谁有护城河。

3. **无人值守交付**：Claude Code + skills 是"人盯着的 pair programmer"。企业要的是"提交需求→睡一觉→拿到带审计签名的交付件"。前者 60-70%，后者缺的 30-40% 全是运行时。

4. **批量迁移经济性**：团队要迁移 200 个 CUDA 算子。通用 agent 一次一个、对话式、需人盯着。批量控制器能夜间跑 200 个、早上拿到失败分类（47 失败：12 tiling / 20 precision / 15 compile）——10-50 倍效率差，真实 ROI。

5. **CI/CD 集成**：PR 时自动触发 review+UT+ST+profiling 子集。这是"算子开发的 CI"，任何 DevOps 团队都需要，且 CI 跑代码不跑 prompt，skills 进不来。

### 4.3 给投资的"一句话"

> 华为正在把算子开发的**知识层**商品化（cannbot-skills + 算子数据集），但明确不做**运行时层**（他们要让所有 agent 都能用 skills，所以不绑运行时）。我们做运行时——把他们的编排配方变成确定性、可审计、可批量化、能调度 NPU 的引擎。这是华为留给生态、且只能用代码填的一层。

### 4.4 投资人的致命问题与回答

**问**："为什么不直接用 Claude Code + cannbot-skills？"

**答**："单算子可以。但试试在没有运行时的情况下，让它在夜里迁移 200 个算子、崩溃自动恢复、产出带审计签名的交付件、在 4 卡 NPU 上排队跑 profiling——这 4 件事 Claude Code 结构性做不到，而这正是企业愿意付费的。"

---

## 第五部分：对开发团队的技术定位

### 5.1 ❌ 不要做的（重新发明轮子）

- 不要重写 52 个 skill——直接 `git submodule` 引 cannbot-skills
- 不要造通用 agent 框架——claude-code/hermes 都证明 ReAct + transcript 够强
- 不要自研 LLM provider 适配——已有 6 个

### 5.2 ✅ 要做的（4 个不可缩减运行时模块）

```
                    ┌─────────────────────────────┐
                    │  cannbot-skills（华为维护）  │ ← submodule 引入，不重写
                    │  52 skills + 9 编排插件      │
                    └──────────────┬──────────────┘
                                   │ 作为知识输入
                    ┌──────────────▼──────────────┐
                    │  AIAgent ReAct 核心（已有）  │ ← 保留，145 行
                    └──────────────┬──────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
   ┌────────────────┐    ┌──────────────────┐   ┌─────────────────┐
   │ 阶段机+checkpoint│    │  NPU 调度器       │   │ 批量作业控制器   │
   │  (崩溃恢复)     │    │  (设备锁/排队)    │   │  (200 算子并行)  │
   │  ~600-900 LOC   │    │  ~1200-2000 LOC  │   │  ~800-1400 LOC  │
   └────────────────┘    └──────────────────┘   └─────────────────┘
            └──────────────────────┴──────────────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │  审计 + CI/CD（~900-1600）    │
                    └──────────────────────────────┘
```

### 5.3 与既有重构决策报告的对齐

本评估**不推翻** [docs/brainstorms/op_agent_refactor_decision_report.md](op_agent_refactor_decision_report.md) 的技术结论（LangGraph 分层采用、垂直切片、8.5 周）。它**重新定位项目价值**：

| 维度 | 旧定位 | 新定位 |
|---|---|---|
| 一句话 | 算子开发 agent | 算子开发的运行时引擎 |
| 竞争态势 | 红海（vs 通用 agent + skills）| 蓝海（华为留白层）|
| 知识资产 | 自研（必败）| 消费 cannbot-skills |
| 代码资产 | 全栈 | 4 个运行时模块（~3000-5000 LOC）|
| 护城河 | 领域知识（弱）| NPU 调度 + 批量 + 审计（强）|

---

## 第六部分：风险登记（对投资团队必须坦白）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **华为可能转向** | 今天不做运行时是为了最大化 skills 覆盖面。若某天认为运行时是必争之地，会直接做 CodeArts 算子版 | 我们的运行时**供应商中立**（支持非昇腾），且比华为云更轻、更嵌入开发者本地工作流 |
| **cannbot-skills 是依赖非资产** | 产品依赖华为维护的 skills。若华为改 license/格式/停止维护 | skills 是 markdown+YAML 可 fork；运行时价值独立于 skills 内容 |
| **市场窗口收窄** | KADC 2026 数据集 + eval set 会吸引更多第三方 | 现在做，抢占"运行时层"心智 |
| **技术可行性未验证** | NPU 调度、批量迁移、崩溃恢复的复杂度可能超估 | 先做 Spike 0（见重构报告），验证 3 个最大未知数 |

---

## 最终裁决

| 立场 | 判定 |
|---|---|
| 通用 coding agent + cannbot-skills 够用吗？ | **单算子够，团队/批量/企业不够** |
| 必须自研算子 agent 吗？ | **不是"agent"，是"运行时引擎"**——且只有这个角度站得住 |
| 能说服投资/开发团队吗？ | **能，前提是重构论据**：放弃"领域知识/算子 agent"卖点，押注"华为留白的运行时层" |

**底线**：原项目"算子开发 agent"定位已被 cannbot-skills 大幅削弱，但**运行时引擎**这个重新定位是真实、可防守、且华为结构性不会做的。这是定位转向，不是项目取消。

---

## 参考来源

### cannbot-skills 仓库
- 官方仓库 — https://gitcode.com/cann/cannbot-skills
- canonical 镜像 — https://gitcode.com/cann/skills
- CANNBot Learning Week 回顾（2026-04-21）— https://ai6s.net/69e74c400a2f6a37c5a14f22.html

### 华为官方工具
- MindStudio — https://www.hiascend.com/software/mindstudio
- 算子开发场景 — https://www.hiascend.com/developer/operator
- ModelArts — https://www.huaweicloud.com/product/modelarts.html
- CodeArts 码道公测（2026-02-26）— https://www.huaweicloud.com/news/2026/20260226150052593.html

### 华为生态信号
- KADC 2026 算子数据集 + eval set（2026-05）— https://m.c114.com.cn/w126-1310929.html
- Ascend 算子自动生成 Agent 2026 公告 — https://m.tech.china.com/redian/2026/0323/032026_1830837.html
- TileLang AscendNPU IR（2026-05）— https://www.hiascend.com/developer/techArticles/20260506-1
- Triton-Ascend — https://github.com/triton-lang/triton-ascend

### 内部参考
- 重构决策报告 — [docs/brainstorms/op_agent_refactor_decision_report.md](op_agent_refactor_decision_report.md)
- 原架构方案 — [docs/brainstorms/op_agent_architecture_proposal.md](op_agent_architecture_proposal.md)
- hermes-agent（0 框架，自定义 ReAct + SQLite checkpoint）— `/Users/huangshilei/Documents/pythonprojects/hermes-agent`
- claude-code（0 框架，JSONL + ReAct + plan mode）— `/Users/huangshilei/Documents/pythonprojects/claude-code`
