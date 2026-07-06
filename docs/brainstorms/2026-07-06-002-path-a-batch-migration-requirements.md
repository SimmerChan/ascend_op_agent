---
title: "路径 A:模型级批量迁移(P0 现状已闭环 + 阶段二:OpAdapter lib + batch_migrate CLI)"
type: feature
status: brainstorm-complete
date: 2026-07-06
origin: team_goals.xlsx (H2 工作目标 B. 模型级批量迁移), docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md (P1 路径 A)
---

# 路径 A:模型级批量迁移(阶段二)

## Summary

新增 `OpAdapter` Python lib + `python -m ascend_op_agent.cli.migrate` CLI,作为 Path A 的**新增产品面**(非 P1 补丁),在现状 P0/P1 单算子闭环基础上扩展为 model-level analyze → batch_migrate。给定 PyTorch model,识别 NPU 不支持的自定义算子,逐个复用路径 B 迁移到 AscendC(或 fallback 标 PyTorch native API),在 910B 真编译 + ST 驱动精度验证。

不重写现状编排器;不重写路径 B;不重写 ST 驱动 — 全部复用,只加两件事:
1. `analyze_unified_op` lib API(model → unsupported ops 列表)
2. `batch_migrate` CLI(lib wrapper + 910B 编排)

## Problem Frame

完成一个模型迁移到 NPU 后,分析模型里**哪些自定义算子 NPU 不支持**。现状只能逐个试:跑 model → 报错 → 查文档 → 改写 → 再跑,周而复始。**P0 已闭环单算子迁移(v1.0.0 已发,见 CHANGELOG / ship_ready.py 4 步全过),缺 model-level analyze 入口和批量编排**。

## 角色(Actors)

| 角色 | 描述 | 关注点 |
|------|------|--------|
| **GPU 算子迁移工程师** | 已有 PyTorch model 准备迁到 NPU | 快速识别"哪些算子要改、哪些能直接用" |
| **NPU 应用研究员** | 实验性模型(可能含 NPU 不支持的算子) | 不重复搭 NPU 编译环境,让 agent 跑精度验证 |

## 流程图

```
用户 CLI
  │
  ▼
$ python -m ascend_op_agent.cli.migrate --model model.pt --output /tmp/out
  │
  ▼
OpAdapter.analyze_unified_op(model)
  │
  ▼
[1/N] analyze: torch_npu.frontend 解析 → 列出 5 个 unsupported op
  │
  ▼
[2/N] migrate: 对每个 op 调现状路径 B (CUDA→AscendC)
  │      PhaseRunner + ST 驱动
  │      编译 + 10/10 precision 验证
  │
  ▼
[3/N] done. 5/5 migrated to /tmp/out
```

## Use Cases

### UC1:Analyze + 报告(独立 use case)

- 角色: GPU 算子迁移工程师
- 入口: `python -m ascend_op_agent.cli.migrate --model model.pt --report-only`
- 退出: 输出兼容性报告(每个 unsupported op: name, schema, recommended action)
- 不调用 batch_migrate,只做识别。

### UC2:Analyze + BatchMigrate(全流程)

- 角色: GPU 算子迁移工程师
- 入口: `python -m ascend_op_agent.cli.migrate --model model.pt --output /tmp/out`
- 退出: `/tmp/out` 下的 N 个 AscendC 工程 + 报告(每个 op: origin, target, result)

### UC3:Migrate fallback to PyTorch native

- 角色: NPU 应用研究员
- 入口: 同 UC2,但 `--strategy native-preferred`
- 策略明细: native-preferred = 先查 `torch.aten` / `torch.nn.functional` 等价 op → 命中则 passthrough(标 `recommended="native"`);未命中 → 走路径 B migrate(`recommended="migrate"`)
- 退出: `/tmp/out` 下混合产物 —— passthrough op 标 native,migrated op 出 AscendC 工程 + 报告

## Requirements

### R1:`OpAdapter.analyze_unified_op` lib API

- 输入:`torch.nn.Module` 实例 **或** `model.pt` / `model.onnx` 路径
- 输出:`list[OpReport]`(每个含 op_name / schema / op_type / recommended: `migrate` | `native` | `passthrough`)
- analyze lib 单元粒度延期到 plan 调研时定(P0 调研过 `torch_npu.frontend` 的兼容性表能力;具体复用方式 + 粒度看 plan)

### R2:`OpAdapter.batch_migrate(ops, output_dir)` API

- 输入:`list[OpReport]`(只处理 `recommended="migrate"` 的) + 输出目录
- 复用现状路径 B: batch_migrate 是 thin loop —— per-op 调 `PhaseRunner.invoke(op)` + `NpuExecutor` + `ST 驱动` 编译 + 精度验证,自身只做 loop / 累积 MigrationResult / continue-on-fail(不重写 invoke 内部)
- 输出:`list[MigrationResult]`(每个 op: path/compile_status/precision_result)

### R3:`python -m ascend_op_agent.cli.migrate` CLI

- 标志: `--model PATH --output DIR [--report-only] [--strategy native-preferred]`
- 两阶段:analyze (R1) → batch_migrate (R2, 可选 `--report-only` 跳过)
- 进度打印: `[1/N] analyze... [2/N] migrate op1... ✓/✗ [3/N] done.`
- 退出码:0 = 全部 pass,1 = 有 fail

### R4:910B 真编译 + ST 驱动验证(复用 P0)

- 每个 migrated op 在 910B ops_pt 容器内 `build.sh --soc=ascend910b` + `msOpUT run` 精度验证
- 阈值:10/10 cases pass(同 P0 现状)
- 失败不阻断后续 op(累积报告,继续 batch 内下一个 op)

### R5:不重写现状

- 不重写 `PhaseRunner` (P0/P1 已完成)
- 不重写路径 B 单算子迁移
- 不重写 ST 驱动
- **OpAdapter 仅做"识别 + 批量编排"**,不替换 P0 任何已有路径

## Non-Goals(Out of Scope)

- 性能优化(不是路径 A 目标;性能由 torch_npu.profiler 处理)
- 自动模型转 Ascend(用户已有 NPU 跑通的需求;只做算子适配)
- 路径 A 的 OpAdapter 自身不写 AscendC kernel(继续调 P0 路径 B)
- analyze lib 输出格式最终延期到 plan 调研定(本 brainstorm 不强定)
- CI / 质量门集成(batch_migrate 作为模型迁移 CI gate 的能力,留 plan / 未来评估,本期不做)

## Open Questions

1. `analyze_unified_op` 报告粒度最简 list[str] vs 完整 OpReport?(plan 调研定)
2. 批量规模 5/50/500 ops 对 OpAdapter API 是否有性能要求?(默认无,单机 OK)
3. `native-preferred` fallback 用什么策略选 PyTorch native API?(plan 调研)
4. ~~analyze + batch_migrate 是否拆两个独立命令 / 一个组合命令?~~ → **已定:组合命令;`--report-only` 跳过 batch**(原延期项,设计已闭环)
5. ~~analyze 阶段如果某个 op 不可识别(返回 unknown_op_type),是否报错或 warn+ skip?~~ → **已定:warn + skip + 累积到报告**(对齐 R4 continue-on-fail,batch 不因单 op 未知类型而中断)

## Success Criteria

- 5 个典型 PyTorch model demo 跑 OpAdapter.analyze_unified_op,识别 unsupported ops 准确率 ≥ 80%(对比手工标注;阈值依据 = Path A 目标是"识别优先"非完美识别,允许 plan 实测后校准)
- batch_migrate 5 ops demo:5/5 编译成功 AND ≥4/5 精度验证 pass(每 op 10/10 cases;允许 1 op fallback,与 R4 continue-on-fail 一致)
- 单个 op batch_migrate 端到端 ≤ 5 min(P0 实测单 op e2e ~2 min Minimax,含 LLM codegen + 910B 编译 + ST 精度;5 min 为 batch 上限余量)
- analyze lib 单测覆盖率 ≥ 80%

## 来源与依赖

- P0 plan: [docs/plans/2026-06-23-001-feat-op-runtime-engine-plan.md](../plans/2026-06-23-001-feat-op-runtime-engine-plan.md) — P1 路径 A 基础
- P1 plan: [docs/plans/2026-06-30-001-feat-p1-production-readiness-plan.md](../plans/2026-06-30-001-feat-p1-production-readiness-plan.md) — 8 个 unit 已 ship-ready
- cannbot-skills: [https://gitcode.com/cann/cannbot-skills](https://gitcode.com/cann/cannbot-skills) — 路径 B 单算子迁移的基础 skill
- 现状路径 B 代码: `src/ascend_op_agent/orchestrator/graphs/migration.py` + `src/ascend_op_agent/orchestrator/nodes/migration.py`(单算子 CUDA→AscendC,已闭环)
- ST 驱动(910B): [docs/e2e/2026-06-27-st-driver-spike-report.md](../e2e/2026-06-27-st-driver-spike-report.md)
