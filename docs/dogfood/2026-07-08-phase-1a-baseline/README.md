# Dogfood Phase-1a Baseline — T0

**日期**: 2026-07-08
**起点**: 一期-a(U1-U4 task store + rollup + progress + dispatch + CLI + falsifier)merge 到 develop 后,首次 dogfood 开始前。
**目的**: 采集任务管理层的 falsifier baseline,作为 2 周后 go/no-go 二期-b 的对照基准。

## 一期-a dogfood subset

CLI `task` 子命令组(`cli.py:727-825`),纯本地 sqlite,**不需要连接 910B**:

| 命令 | 行为 | 路径 |
|------|------|------|
| `task new <type>` | 建 task + 设 active | `cli.py:781-792` |
| `task list` | 列任务 + state + threads | `cli.py:761-778` |
| `task select <id>` | 切换 active | `cli.py:795-806` |
| `task progress [id]` | 查进展(active 或指定) | `cli.py:809-825` |

TYPE ∈ `{migrate, analyze, optimize, develop}`。

`falsifier_baseline.py` 只读 `tasks.db`,输出 JSON,**零 SSH / 零 LLM / 零 NpuExecutor**。

## 依赖隔离

- 生产路径 `~/.ascend_op_agent/tasks.db` — dogfood 期间真实使用,不预先建
- 烟囱测试 `/tmp/dogfood_tasks.db` — 4 条命令端到端验证,验证后归档
- 生产路径 baseline 在 T0 时刻 `task_count=0, active=null`,**确认无残留**

## 文件

| 文件 | 内容 |
|------|------|
| `t0_prod_baseline.json` | T0 时刻生产路径 baseline(`task_count=0`) |
| `t0_smoke_baseline.json` | T0 烟囱测试后状态(`task_count=3`,3 类型各 1) |
| `commands.md` | 烟囱测试执行的命令序列 |

## R16 gate 规则

> dogfood 2 周后,若 `spontaneous_task_switches < 阈值` 且 `context_juggling_complaints = 0` → task 层痛点未验证 → **abandon**(本 gate 是 task 层 kill-switch)。

`spontaneous_task_switches` 反映 `set_active` 调用次数(多任务并行真实发生);
`context_juggling_complaints` 反映用户报告"上下文混乱/进展不可查"次数。

T0 = 0,0(基线)。

## 验证状态

- [x] `task new develop` → ✓ `31f7b2cfe447` (active)
- [x] `task new migrate`  → ✓ `73eca1201916`
- [x] `task new analyze`  → ✓ `401a95ceba28`
- [x] `task list`         → 3 行,state=draft, threads=0
- [x] `task select <id>`  → active 切换正确
- [x] `task progress`     → active 模式 + 指定 id 模式都 OK
- [x] `task select bogus` → exit 1, `unknown task: nonexistent`
- [x] `task new bogus`    → exit 1, `unknown task type`
- [x] `falsifier_baseline.py` → 纯本地,JSON 正确输出

## 不需要 910B

一期-a 代码路径 0 处 import `npu_exec / ssh / 192.168.9.105`。
只有走顶层 `ascend-op-agent run 'op: ...'` 才会触发 SSH(P0/P1 ship gate 范畴,不在 dogfood 范畴)。