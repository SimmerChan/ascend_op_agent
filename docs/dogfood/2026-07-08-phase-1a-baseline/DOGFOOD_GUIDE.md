# Dogfood 操作指南 — Phase-1a

**你从今天起是用户,不是开发者。**

## 0. 这是什么(直白版)

`dogfood` = "eat your own dog food" = 你用自己的软件做自己的真事。

Phase-1a 给你 ship 了 4 条 CLI 命令:
- `task new <type>` 建任务
- `task list` 看所有任务
- `task select <id>` 切当前 active 任务
- `task progress [id]` 看任务进展

**dogfood 是你真用这 4 条命令管你的真实工作**,看是否真的帮上了你 juggling 多任务的痛点。

2 周后看结果 → 决定进 phase-1b 还是删 task 层(R16 gate)。

## 1. 你要做的事(2 分钟/天)

### 第一次(今天,~5 分钟 setup)

打开 terminal,跑:

```bash
# 建 3-5 个真实会做的任务,代表你日常工作的几种类型
ascend-op-agent task new develop    # 例:开发新算子
ascend-op-agent task new migrate    # 例:从 CUDA 迁某个 op
ascend-op-agent task new analyze    # 例:分析某个 op 的性能瓶颈

# 看初始状态
ascend-op-agent task list
```

输出像这样:
```
abc12345 develop  state=draft threads=0    ← 当前 active(刚建的)
def67890 migrate  state=draft threads=0
ghi11111 analyze  state=draft threads=0
```

**完事。今天可以开始了。**

### 日常使用(每天 1-2 次,~30 秒)

**场景 A:开始干一个新任务(自发想切)**

之前在算子 A,现在开始干算子 B:
```bash
ascend-op-agent task select def67890    # 切到 migrate 任务
ascend-op-agent task progress           # 看 migrate 当前到哪了
```
> **这一刻:`spontaneous_task_switches` +1**(你自发地切换 active)

**场景 B:打开 terminal 忘了当前在哪个任务**

```bash
ascend-op-agent task progress
```
> **如果这一刻你之前确实"不知道在干哪个任务",`context_juggling_complaints` +1**(说明 task 命令帮你解决了痛点)

**场景 C:临时有个新活儿要建任务**

```bash
ascend-op-agent task new optimize    # 建一个 optimize 任务
```

**场景 D:今天就处理一个任务,没切过**

不用操作命令。明天继续。

### 不需要做的事

- ❌ 不需要为了"凑 metric"切任务(那不是自发)
- ❌ 不需要每天都 `task select` 同一任务刷数
- ❌ 不需要填假嘟囔记录
- ❌ 不需要 commit / push / 跑测试 / 看代码 — 你是用户
- ❌ 不需要 ssh 到 910B 用真编译(phase-1a 4 条命令纯本地)

### 出错 / 卡住时

```bash
# 1. 看状态
ascend-op-agent task list

# 2. 看具体任务
ascend-op-agent task progress

# 3. 仍卡住 → 把报错截屏,在 dogfood-log.md 写"问题记录"段
```

## 2. 反馈记录(每天/每周,~2 分钟)

打开 `docs/dogfood/2026-07-08-phase-1a-baseline/dogfood-log.md`,每次有事件就 append 一段。

### 模板

```markdown
## 2026-07-09 周三

### 操作记录
- task new: 1 次(migrate - 算子 X)
- task select: 3 次(op A → op B → op C;开发完切去测迁移)
- task progress: 5 次(其中 1 次是忘了在哪个任务)
- task list: 1 次

### Metric 累计(从 7-08 起,含今日)
- spontaneous_task_switches: 3
- context_juggling_complaints: 0

### 主观感受(自由写,几条都行)
- task progress 看 state 信息挺有用
- 但还是要切回 IDE 看代码,task 命令不能替代
- 今天没找到任何 task 命令解决不了的问题,但也没发现它特别有用

### 是否有痛点被解决?
- 之前:开 3 个 terminal 记不住哪个是哪个
- 现在:task progress 一查就知道 → **有改善**
```

### 关键 metric 累计(每周算一次)

到周日或每隔几天,打开 log 数 grep:

```bash
grep spontaneous docs/dogfood/2026-07-08-phase-1a-baseline/dogfood-log.md | tail -10
```

## 3. T1 采集(2 周后,~2026-07-22)

跑这一行(机器能跑就行,不需要 910B):

```bash
PYTHONPATH=src python scripts/falsifier_baseline.py \
  > docs/dogfood/2026-07-22-phase-1a-baseline/t1_prod_baseline.json
```

把 dogfood-log.md 也复制一份到 `docs/dogfood/2026-07-22-phase-1a-baseline/dogfood-log.md`(snapshot)。

## 4. GO / NO-GO 怎么判

打开 `docs/dogfood/2026-07-08-phase-1a-baseline/README.md` 看 R16 gate 段。

| `spontaneous_task_switches` | `context_juggling_complaints` | 决策 |
|---:|---:|---|
| ≥ 5 | 任意 | **GO** → 修 🔴 3 review → merge phase-1b → 二期-b dogfood |
| < 5 | = 0 | **NO-GO** → 删 task 层,删 `feat/task-mgmt-phase-1b` 分支 |
| < 5 | > 0 | **GO**(痛点真实,只是没频繁切换) |

(阈值 ≥5 是我建议的起点,你可以按真实数据调)

## 5. 何时问 Claude / 我

- 命令报错
- 不知道该 `task new` 还是 `task select`
- 想加新功能(不在 dogfood scope — 写 log 的"建议"段,等 2 周后再决定)
- 想看代码、改代码、跑测试 — **dogfood 期间不要**(你是用户,不是开发者)

## 6. TL;DR

| 你 | 我 |
|----|---|
| 每天 1-2 次真用 `task ...` 命令管理真实工作 | 准备好环境 + 写好 log 模板 + 2 周后跑 T1 |
| 每天 / 几天写一段 dogfood-log.md | 数 metric、判 GO/NO-GO |
| 不用 910B / 不用 commit / 不用看代码 | 修 review 🔴 3 个 / merge / 二期-b |
