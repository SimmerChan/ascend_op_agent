---
title: "路径 A v2:模型级批量迁移(编排 npu-model-migration SKILL + 路径 B 兜底)"
type: feature
date: 2026-07-06
topic: path-a-model-migration
origin: team_goals.xlsx (H2 目标 B. 模型级批量迁移), docs/brainstorms/2026-07-06-002-path-a-batch-migration-requirements.md (v1, 前提有误已废弃)
---

# 路径 A v2:模型级批量迁移

## Summary

给定 Python 模型 repo,Path A 编排 npu-model-migration SKILL 的 7 阶段在 model 级别跑:实测(`transfer_to_npu` + 跑脚本 + 可选 GPU profiling)诊断 unsupported op,按源类型分流迁移 —— pure PyTorch 跑通即 passthrough;报错但有 `torch.aten` 等价即 native;GPU triton op 迁昇腾 triton;custom CUDA op 优先用 PyTorch 原生 API 等价重实现(封装 nn.Module),不可行再由 LLM 推荐 AscendC/triton kernel、用户 HITL 确认。custom 类复用 P0 路径 B(PhaseRunner + cannbot skill + 910B 真编译 + ST 精度),输出 NPU 适配后脚本 + per-op 报告。一期建终态全流程,HITL 只在方案设计后确认一次。

---

## Problem Frame

GPU 工程师把 PyTorch 模型迁 NPU 时卡在"哪些算子不支持 / 哪些要改"。现状只能手动跑 → 报错 → 查文档 → 改 → 再跑,周而复始。npu-model-migration SKILL 已把这个人工流程沉淀成 7 阶段指南,带 AutoInt / DeepFM / DIN / Wide&Deep 等成功案例。痛点是循环本身耗时、经验不固化、批量模型迁不动。

v1 brainstorm 把"模型"误解成 `.pt` 权重文件 + 用 `torch_npu.frontend` 静态解析列 unsupported op —— 错。模型迁移的输入是原始 Python 脚本 / repo;算子不支持 / 瓶颈分析靠**实测**(跑脚本看报错 + `torch.profiler` profiling),不是静态解析。v2 基于此修正,编排对象从"静态扫描"换成"跑 SKILL 7 阶段诊断循环",路径 B(单算子 kernel 迁移)降级为 custom 类的兜底通道。

> **batch 范围注**:一期 "batch" = 单 model 内多 op 批量迁移;batch-of-models(一次迁多个 model)在二期。H2 目标 B 的"批量"终态含两者,本期先做前者。

---

## Key Decisions

- **一期建终态全流程(含路径 B 兜底),不分期** —— 接受 custom kernel 迁移的不确定性(LLM 写 AscendC/triton 成功率未知),要一次到位;失败按"标 fail + continue"兜,不阻断 batch。
- **实测驱动诊断,非静态解析** —— `transfer_to_npu` 快速尝试 + 跑脚本收集报错 + 可选 GPU profiling 交叉验证。op 是否支持由"跑"判定,不由兼容性表查。
- **op 按源类型分流,不按"是否支持"二分** —— passthrough / native / migrate-triton / migrate-cuda 四类,各自走不同迁移路径。
- **migrate-cuda 优先原生 API 等价实现(nn.Module),kernel 作兜底** —— 能用 PyTorch 原生 op 组合表达 CUDA 算子语义的,先封装 `nn.Module` 子类替换(不写 kernel、不经路径 B、靠模型级验证),最大化规避 LLM-kernel 成功率风险;表达不了的再走路径 B 写 AscendC/triton kernel。
- **单点 HITL:方案设计后确认一次** —— 对齐 SKILL 阶段 2.3 强制确认 + PhaseRunner 既有 HITL 模式;阶段 4-7 全自动,保 batch 流不中断。
- **失败 = 标 fail + continue + 写报告,不人工补 kernel** —— 一期接受 kernel 迁移失败作为合法产物,不引入人工兜底通道。
- **复用 P0/P1,不重写** —— PhaseRunner / NpuExecutor / ST 驱动 / cannbot-skills 知识层 / CheckpointStore 全复用;Path A 只加 model-level 编排 + 实测 analyze + 7 阶段 dispatch。

---

## Actors

| ID | 角色 | 职责 |
|----|------|------|
| A1 | GPU 算子迁移工程师 | 提供 repo + 入口脚本(任意 .py/.sh),方案阶段 HITL 确认 op 分类 + CUDA 目标 |
| A2 | Path A agent | 编排 7 阶段、实测诊断、op 分类、迁移调度、报告生成 |
| A3 | 路径 B / PhaseRunner | 单算子 kernel 迁移(CUDA→AscendC / Triton→昇腾 triton),migrate 类调用 |
| A4 | 910B NPU 环境 | 真编译 + ST 驱动精度验证执行环境(SSH → docker exec ops_pt) |

---

## Key Flows

- F1. 端到端 model 迁移
  - **Trigger:** A1 提交 repo local path + 入口脚本(任意 .py/.sh,可选 GPU profiling)。
  - **Actors:** A1, A2, A3, A4。
  - **Steps:** 见下图 7 阶段 + op 分流。
  - **Outcome:** `npu/` 并行目录下的 NPU 适配脚本 + per-op 报告 + SKILL 格式迁移报告。

```mermaid
flowchart TB
  IN[输入: repo + 入口脚本(.py/.sh) + 可选 GPU profiling] --> P1[阶段1 目标分析]
  P1 --> P15[阶段1.5 transfer_to_npu 快速尝试]
  P15 --> P2[阶段2 方案设计]
  P2 -. HITL 确认 .-> P2H[(op 分类 + 改动清单 + CUDA 目标推荐)]
  P2H --> P3[阶段3 代码迁移 per-op dispatch]
  P3 --> D{op 源类型}
  D -->|passthrough| S1[skip]
  D -->|native| S2[device/API adapt + aten 替换]
  D -->|migrate-triton| S3[路径B: triton → 昇腾 triton]
  D -->|migrate-cuda| S4{cuda target}
  S4 -->|native-composition 优先| S4a[nn.Module 替换]
  S4 -->|AscendC/triton| S4b[路径B kernel]
  S1 & S2 & S3 & S4a & S4b --> P4[阶段4 NPU 验证: 模型级 run / op 级 compile+ST]
  P4 --> P5[阶段5 调试迭代]
  P5 --> DEC{通过?}
  DEC -->|否, 迭代 ≤ 5 次| P3
  DEC -->|否, 超限| FAIL[标 fail + continue]
  DEC -->|是| P6[阶段6 报告]
  FAIL --> P6
  P6 --> OUT[输出: npu/ 适配脚本 + per-op 报告]
```

---

## Requirements

### 输入与诊断

- R1. 输入为 repo local path + 用户指定的入口脚本(任意 .py/.sh)+ 单 model;可选 GPU `torch.profiler` 导出(chrome trace)作辅助诊断输入。框架型 repo(含多 model,如 TorchEasyRec)一期支持:先问用户选哪个 model 再迁。git url 自动 clone 与多 model 框架批量(一次迁多个)不在一期。
- R2. 实测 analyze(主):`transfer_to_npu` 快速尝试 → 跑入口脚本收集报错(脚本首错即 abort,需 continue-on-error harness 或静态 aten 等价查询作枚举加速器,避免只露 1 个 op)→ 合并可选 profiling 交叉验证报错定位 → 输出完整 op 诊断列表。静态表查询仅作枚举加速,不替代实测判定。
- R3. op 按源类型分四类:passthrough(跑通)/ native(报错但有 `torch.aten` 等价)/ migrate-triton(GPU triton op)/ migrate-cuda(custom CUDA op)。**优先级**(一个 op 可落入多类时):passthrough > native > migrate-triton > migrate-cuda —— 有 aten 等价优先走 native(便宜),无等价再进 migrate 类(migrate-cuda 优先原生 API 等价实现,其次 kernel)。

### 迁移执行与验证

- R4. passthrough 类不处理(`transfer_to_npu` 已搞定),仅在报告标记。
- R5. native 类自动做 device/API adapt + `torch.aten` 等价替换 + `transfer_to_npu` 注入(机械改动,对齐 SKILL 阶段 3.1/3.3)。
- R6. migrate-triton 类:GPU triton op → 昇腾 triton,复用路径 B,参考 cannbot `ops/triton-op-coding` skill。
- R7. migrate-cuda 类:custom CUDA op 按可行性三选一 —— (1) **PyTorch 原生 API 等价实现**(优先:LLM 分析算子语义,用原生 op 组合重实现,封装 `nn.Module` 子类替换,不写 kernel / 不经路径 B,靠模型级验证);(2) AscendC;(3) triton。方案阶段 LLM 推荐优先级 + 用户 HITL 确认;(2)(3) 复用路径 B(参考 cannbot `ops-lab/cuda2ascend-simt`)。
- R8. 每个 migrated op 在 910B `ops_pt` 容器内 `build.sh --soc=ascend910b` 真编译 + ST 驱动精度验证(10/10 cases pass,同 P0)。

### 编排与 HITL

- R9. 编排 npu-model-migration SKILL 7 阶段:目标分析 → `transfer_to_npu` 快速尝试 → 方案设计 → 代码迁移 → NPU 验证 → 调试迭代 → 报告。
- R10. 单点 HITL:方案设计阶段后确认一次(op 分类 + 改动清单 + CUDA 目标推荐),确认后阶段 4-7 全自动,不再逐 op 打断。
- R11. 调试迭代上限 5 次/op(对齐 SKILL 阶段 5),超限标 fail + continue。**重分类通道**:若 native 类 op ST 连续失败 ≥3 次(说明 aten 不等价),触发重分类到 migrate-cuda/triton 走路径 B —— 避免单点 HITL 误分类在 run 内不可恢复。

### 输出与失败处理

- R12. 输出到并行目录(`npu/` 子目录,不改原码,对齐 SKILL 约束):NPU 适配脚本 + per-op 报告 + SKILL `migration-report-template` 格式迁移报告。
- R13. 失败处理:kernel 迁移失败 / 编译失败 / 精度不过 / 迭代超限 → 标 fail + continue batch 内下一个 op + 写进报告(报告须含末次编译错误 / 末次精度 delta / 迭代次数,作可接手交接物而非墓碑);不人工补 kernel。

### 复用边界

- R14. 不重写 P0/P1 现状(PhaseRunner / NpuExecutor / ST 驱动 / cannbot-skills 加载 / CheckpointStore);Path A 做 model-level 编排 + 实测 analyze + 7 阶段 dispatch + native 类 mechanical-adaptation(device/API 替换,不写 kernel),不自造 AscendC/triton kernel writer(custom 类继续调路径 B)。

---

## Acceptance Examples

- AE1. **Covers R2, R3, R4.** Given pure PyTorch op + `transfer_to_npu` 跑通无报错 → 分类 passthrough → 不处理,报告标记。
- AE2. **Covers R2, R3, R5.** Given pure PyTorch op + 跑报错 + 存在 `torch.aten` 等价 → 分类 native → 自动 device/API adapt + 等价替换。
- AE3. **Covers R3, R6.** Given GPU triton kernel source → 分类 migrate-triton → 路径 B 迁昇腾 triton(参考 cannbot triton skill)。
- AE4. **Covers R3, R7, R10.** Given custom CUDA op + 无 native 等价 → 分类 migrate-cuda → 方案阶段 LLM 优先评估原生 API 等价实现可行性(可行→封装 `nn.Module` 替换,不走路径 B),不可行→推荐 AscendC/triton + 用户 HITL 确认 → 路径 B 迁移。
- AE5. **Covers R8, R11, R13.** Given migrated op 编译或精度连续失败超 5 次 → 标 fail + continue 下一个 op + 写报告,不阻断 batch、不人工补。

---

## Success Criteria

- 模型 NPU 跑通:≥3 个 model demo 端到端产出能跑的 NPU 脚本(实际跑出训练/推理数值输出,非仅 import 成功);其中 ≥1 个含 migrate-cuda op、≥1 个含 migrate-triton op —— 避免全 passthrough/native 绕过 custom 路径验证(SKILL 案例库外需补含 custom op 的 demo)。
- LLM kernel 功能正常:端到端跑通的 model 里,migrated op 的 kernel 全部通过 ST 精度验证(算子级功能正确)。
- 诊断质量(辅助信号,非门槛):op 源类型分类准确率 ≥80%,对 SKILL 案例做 fixture 校准。
- 自动化增量(辅助):至少 1 个 demo model 记录 agent 迁移耗时 vs 工程师按 SKILL 手动迁移,作单点 baseline 对比(验证自动化价值,非门槛)。

---

## Scope Boundaries

### Deferred for later(二期)

- 分布式 HCCL 适配(一期单卡/推理)。
- 训练循环深度改造(一期训练只做 device 适配层,不改训练逻辑)。
- git url 自动 clone + 多 model 框架批量(一次迁多个 model;框架型 repo 问选哪个已在一期 R1)。
- 性能优化 / 瓶颈修复(归 `torch_npu.profiler` 外部工具)。

### Outside this product's identity

- 自动模型转 Ascend(只做脚本/算子适配,不做模型格式转换)。
- OpAdapter 自身写 AscendC/triton kernel(继续调路径 B,不自造 kernel writer)。
- CI / 质量门集成(留 plan / 未来评估,本期不做)。

---

## Dependencies / Assumptions

- **依赖 P0/P1 闭环能力**:PhaseRunner(`src/ascend_op_agent/orchestrator/state_machine.py`)+ NpuExecutor(`orchestrator/npu_exec.py`,SSH→docker exec ops_pt→build.sh)+ ST 驱动(`orchestrator/nodes/validation.py`)+ cannbot-skills 知识层(`orchestrator/cannbot_loader.py`,消费 `vendor/cannbot-skills`)+ CheckpointStore(`orchestrator/checkpoint.py`)。
- **依赖 cannbot skill**:`cuda2ascend-simt`(CUDA→AscendC)+ `triton-op-coding`(triton→昇腾 triton)。
- **待 plan 验证**:migrate-triton(路径 B 的 triton→昇腾 triton 端到端)在 P0/P1 未验证(P0/P1 验证的是 AscendC 路径);plan 阶段先核实路径 B triton 覆盖,否则 R6 当一期新建而非纯复用。
- **依赖 npu-model-migration SKILL** 7 阶段方法论(gitcode Ascend agent-skills)。
- **假设**:910B 远程环境(192.168.9.105 / ops_pt 容器)可用,免密 SSH 已配。
- **高风险假设**:`transfer_to_npu` 适配当前 torch_npu 版本(cann-9.1.0),能处理多数 device API。R4 passthrough + R5 native 全靠它;Success Criteria 含 fixture model 上 device-API 覆盖率核验(自动转换位点数 vs 总数)。
- **高风险假设**:LLM 能为多数 custom op 生成可用 kernel;失败按 R13 兜,不构成阻塞但会影响 migrate 成功率。
- **pre-build spike(一期 contingent on)**:开工前跑现有路径 B 于 N 个真实 custom CUDA op(从 demo model 采样),测 ST pass rate;若低于阈值,"不分期" Key Decision 重开(3-persona 共指风险)。

---

## Outstanding Questions

### Deferred to Planning

- 诊断 fixture 用 SKILL 哪几个案例(AutoInt/DeepFM/DIN/Wide&Deep)?是否要先做成可跑的 GPU baseline 才能验证"实测报错"环节?
- analyze 报告的 op 诊断 schema 字段(op_name / source_type / error / profile_evidence / recommended / target)。
- migrate-cuda target 推荐:原生 API 等价实现可行性判定 + kernel target(AscendC/triton)选择。
- HITL 确认的 UI 形态(复用 PhaseRunner `pending_confirmation` TUI 通道?)。
- 并行目录结构细节(`npu/` 子目录 vs git 分支)。
- 分类器机制选择(静态启发式 / LLM 判定 / 报错解析 / aten-oracle 查询)—— plan 期实现决策,影响分类准确率(≥80%)的可测性。

> cannbot skill 名已核实(`orchestrator/cannbot_loader.py:161-177`):migrate-triton → `ops/triton-op-coding`(+designer/verifier),migrate-cuda → `ops-lab/cuda2ascend-simt`。

---

## Sources / Research

- npu-model-migration SKILL: https://gitcode.com/Ascend/agent-skills/blob/master/official/MindSeriesSDK/RecSDK/npu-model-migration/SKILL.md — 7 阶段方法论 + 案例库(AutoInt/DeepFM/DIN/Wide&Deep)。
- cannbot-skills: https://gitcode.com/cann/cannbot-skills — 路径 B 知识层(cuda2ascend-simt + triton-op-coding),由 `orchestrator/cannbot_loader.py` 加载。
- P0 plan: `docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md` — PhaseRunner / NpuExecutor / ST 驱动基础。
- P1 plan: `docs/plans/2026-06-30-001-feat-p1-production-readiness-plan.md` — production readiness(8 unit ship-ready)。
- Supersedes v1: `docs/brainstorms/2026-07-06-002-path-a-batch-migration-requirements.md` — 前提有误(模型当 .pt 文件 + torch_npu.frontend 静态解析),v2 修正。
