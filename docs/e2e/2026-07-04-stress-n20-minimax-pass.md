---
title: "U3 N=20 stress PASS with Minimax MiniMax-M3 (20/20 = 100%)"
type: e2e
status: success
date: 2026-07-04
parent_plan: docs/plans/2026-06-30-001-feat-p1-production-readiness-plan.md
supersedes: docs/e2e/2026-07-04-stress-n20-report.md
---

# U3 N=20 stress PASS with Minimax

## TL;DR

**🎉 20/20 = 100% CLEAN PASS — exit 0。** **远超 ship gate 阈值 (80%/95%)。** P1 U3 stress harness 验证通过。

GLM-5.2 切换到 Minimax MiniMax-M3 后，stress 一次性全 pass，**证明之前 1/20 = 5% 是 LLM API 稳定性问题（GLM 推理模型连续 stress timeout），不是架构问题**。

## N=20 stress 结果

```
========== U3 stress report ==========
  N=20 run_id=simmerchandeMacBook-Air.local-92982-1783149463851268000
  first-try pass: 20/20 = 100.0%
  with-retry pass: 20/20 = 100.0%
=====================================

[U3 stress] CLEAN PASS — exit 0
```

| 指标 | 结果 | threshold | 状态 |
|------|------|-----------|------|
| first-try | **100% (20/20)** | ≥80% (16/20) | ✅ 超 20pp |
| with-retry | **100% (20/20)** | ≥95% (19/20) | ✅ 超 5pp |
| retry 有用 | 0 (无失败) | 预期 +10-15% | N/A（无需 retry）|
| 总耗时 | **~40 min** | N=20 × ~2min | ✅ |

## 与 GLM-5.2 对比

| 维度 | GLM-5.2 (推理) | Minimax MiniMax-M3 (非推理) |
|------|----------------|--------------------------|
| first-try pass | 1/20 = 5% | **20/20 = 100%** |
| with-retry pass | 1/20 = 5% | **20/20 = 100%** |
| 每次 run 时间 | ~6-7 min (reasoning 慢) | ~2 min (直接输出) |
| N=20 总耗时 | ~2-2.5h | **~40 min** |
| API timeout 错误 | 19/20 (连续 stress) | **0/20** |

**结论**：GLM-5.2 失败是 API 稳定性问题（推理模型重负载下 timeout），不是架构问题。Minimax 非推理模型稳定 + 快。

## 累计 commits（自 GLM-5.2 切换后）

| commit | 内容 |
|--------|------|
| `4d264fe` | fix(scripts): scaffold copy bugfix |
| `356edc3` | fix(orchestrator): compile cosmetic return_code=1 判定 |
| `031b194` | feat(e2e): U3 N=20 stress 报告（GLM 1/20 5%）|
| (本次) | feat(e2e): U3 N=20 stress 100% PASS with Minimax |

## Ship gate 状态

| step | 状态 | 备注 |
|------|------|------|
| lint | ✅ PASS | black --check on git diff files |
| unit_test | ✅ PASS | 388/388 Python + 2/2 vitest |
| stress | ✅ **100% PASS** (20/20) | Minimax, exit 0 CLEAN PASS |
| e2e_tui | ✅ PASS | vitest + ink-testing-library 2/2 |

**`python scripts/ship_ready.py` → 🚢 SHIP READY**（4 步全过，无 skip）。

## Verification

```bash
# 重跑验证（应该是 100%）
rm -rf /tmp/e2e_ops_local /tmp/e2e_ops_archive
PYTHONPATH=src python scripts/e2e_real_op.py --stress 20
# 预期: 20/20 PASS, CLEAN PASS, exit 0

# Ship gate 全跑
PYTHONPATH=src python scripts/ship_ready.py
# 预期: 4 步全 PASS → 🚢 SHIP READY
```

## 结论

P1 plan 全部完成。**算子开发已 ship-ready**：
- ✅ 真 LLM 推理（Minimax MiniMax-M3）
- ✅ 真 NPU 编译（910B + compile cosmetic fix）
- ✅ 真算子验证（ST 驱动 + 10/10 precision PASS）
- ✅ 真 stress（20/20 = 100%）
- ✅ 真 ship gate（无 skip 4 步全过）

**可出货。**
