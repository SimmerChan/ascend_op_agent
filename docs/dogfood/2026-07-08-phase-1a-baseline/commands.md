# 烟囱测试命令序列

T0 时刻在 `/tmp/dogfood_tasks.db` 跑通 4 条 CLI 命令,验证一期-a dogfood subset 端到端可用。

## 环境

- Python: `/usr/local/bin/python3.10`(项目要求 ≥3.10)
- Conda `py311` 未在 PATH,直接用 python3.10
- 工作目录: `/Users/huangshilei/Documents/pythonprojects/ascend_op_agent`
- 分支: `develop`
- 隔离 DB: `/tmp/dogfood_tasks.db`
- 隔离 CK: `/tmp/dogfood_checkpoints.db`

## 序列

```bash
# 1) 建 3 类型 task(验证 new 接受所有合法 type)
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db new develop
# → ✓ created 31f7b2cfe447 (type=develop), set active

PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db new migrate
# → ✓ created 73eca1201916 (type=migrate), set active

PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db new analyze
# → ✓ created 401a95ceba28 (type=analyze), set active

# 2) list(应显示 3 行)
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db list
# 31f7b2cfe447 develop  state=draft threads=0
# 73eca1201916 migrate  state=draft threads=0
# 401a95ceba28 analyze  state=draft threads=0

# 3) progress(active = analyze)
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db progress
# 401a95ceba28 analyze state=draft phase=None

# 4) select develop,再 progress
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db select 31f7b2cfe447
# → ✓ active task = 31f7b2cfe447

PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db progress
# 31f7b2cfe447 develop state=draft phase=None

# 5) 显式 progress 指定非 active id
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db progress 401a95ceba28
# 401a95ceba28 analyze state=draft phase=None

# 6) 错误路径
PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db select nonexistent
# → 'unknown task: nonexistent'  [exit=1]

PYTHONPATH=src /usr/local/bin/python3.10 -m ascend_op_agent.cli \
    task --db /tmp/dogfood_tasks.db --ck /tmp/dogfood_checkpoints.db new bogus
# → 'unknown task type: bogus; expected one of (...)'  [exit=1]

# 7) falsifier baseline(dogfood db)
PYTHONPATH=src /usr/local/bin/python3.10 scripts/falsifier_baseline.py \
    --db /tmp/dogfood_tasks.db > docs/dogfood/2026-07-08-phase-1a-baseline/t0_smoke_baseline.json

# 8) falsifier baseline(prod path,空)
PYTHONPATH=src /usr/local/bin/python3.10 scripts/falsifier_baseline.py \
    > docs/dogfood/2026-07-08-phase-1a-baseline/t0_prod_baseline.json
```

## 结论

所有 8 步均按预期:happy path + 错误路径 + baseline 采集均通过。
一期-a task CLI 在生产路径首次使用时将自动创建 `~/.ascend_op_agent/tasks.db`(TaskStore 启动时 `mkdir parents=True, exist_ok=True`)。