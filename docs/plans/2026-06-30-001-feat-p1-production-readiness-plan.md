---
title: "feat: P1 生产就绪（真交互 + stress + schema + 真硬件）"
type: feat
status: active
date: 2026-06-30
origin: docs/plans/2026-06-26-002-feat-post-p0-completeness-robustness-plan.md (completed)
---

# feat: P1 生产就绪（真交互 + stress + schema + 真硬件）

## Summary

P0（[2026-06-26-002](2026-06-26-002-feat-post-p0-completeness-robustness-plan.md)）9 unit 完成 + 5 gap 验证 = `[U7] 5 gap all satisfied: True`，但均为 **mock demo 级别**（happy path 用 mock executor，后端 RPC mock stdio，TUI 渲染 silent）。P1 是把 demo 转成 ship-ready：真 TUI stdin 交互端到端跑通、CheckpointState schema 加 `skill_loads` 字段、910B 真硬件 N=20 stress ≥95% 通过率。完成后用户跑 `ascend-op-agent run "实现 add"` 能真上线而不是 demo。

## Problem Frame

### P0 留下的 ship-ready 缺口

| 维度 | P0 状态 | 出货后果 |
|------|---------|----------|
| **TUI 真 stdin RPC** | U6 mock stdio pipe + 12 个老 backend 测试，**真 spawn backend + 真 stdin 没跑过** | 用户跑 CLI 可能 hang 在 "thinking"；debug 一周找 "为什么 backend 进程 stdin 没 flush" |
| **TUI 渲染 skill chips** | U8 App.tsx 加 state，**只在 mock backend push 时验证**；真 LLM 跑时芯片是否真到屏未见 | 用户看到 "loaded: []" 以为是 bug，actual silently 数据丢失 |
| **硬件波动** | U0.5 spike 单次 10/10 PASS，**没测 N=20 累计通过率** | partial failure (5/10) 上线后才发现；fix_loop 频繁耗光 max_rounds |
| **CheckpointState schema** | CheckpointStore 用 v1，**新字段 skill_loads 跨 checkpoint 不持久**（仅 ephemeral registry） | 升级 op-agent 后老 checkpoint 反序列化失败；或新字段不写入，重启丢 |
| **真实 CLI stdio** | `useBackendProcess` 启动 backend 子进程（observation 4856），**未端到端验证回环**（用户打字 → 后端响应 → 屏幕刷新） | 用户报告"卡死"，实际是 RPC 通信断 |

### 何时不上

P1 不做：
- A5/950 切换（仅切 `soc_version`；新 plan）
- signal-2 fingerprint（plan R5 P1 已删）
- 模型级批量迁移 / CUDA 迁移深度优化 / 多卡池调度（新 plan）
- Backend GraphQL / REST（仍走 stdio JSON-RPC；plan "Deferred" 段）

---

## Requirements

- **R1 真 TUI stdin RPC 端到端**：`spawn backend → 真 stdin JSON-RPC 发 `agent.run`/`session.resume_with_input` → 收 `backend.ready` + `agent.progress` + `skill.usage` 推送 → screen 更新真发生；ink-testing-library 集成测试覆盖 happy 路径 + interrupted（HITL）+ error 状态
- **R2 CheckpointState schema v2**：`CheckpointStore` 加 `skill_loads: list[SkillLoad]` 字段（persistent，跨 run 可见）；旧 v1 checkpoint 自动迁移到 v2；`from_checkpoint`/`to_checkpoint` 序列化/反序列化 round-trip
- **R3 真硬件 N=20 stress ≥95%**：`scripts/e2e_real_op.py` 跑 N=20 次 reference migration（scaffold=add_example → ST 驱动跑通）；累计 pass rate ≥95%（即 ≥19/20），失败 case 自动记录 stderr 摘要
- **R4 TUI skill chips 真实数据流**：用 ink-testing-library 模拟用户打字 "op: 实现 add" → 后端真跑（用 FakeExecutor 模拟 NPU，避开 910B）→ screen 显示 "loaded: [cuda2ascend-simt], used: [cuda2ascend-simt]"

**Origin actors**: GPU 工程师（single user / 小团队）；算子开发 + 调试

## Scope Boundaries

### In scope
- ink-testing-library 装 + 集成测试套
- CheckpointState schema v2 实现 + 老 v1 迁移 + 序列化测试
- e2e_real_op.py 加 `--stress N` 标志，输出累计 pass rate 报告
- TUI 真 stdin RPC 端到端（spawn backend + 真实 stdin 发 RPC + ink-testing-library 验证 screen）

### Out of scope
- A5/950 复用（仅切 `soc_version`）
- signal-2 fingerprint（已删）
- Backend GraphQL / REST、新协议、新 socket server
- 模型级批量迁移 / 多卡池调度 / CUDA 深度优化（新 plan）
- Web websocket 推送、UI 库切换

### Deferred to Follow-Up Work
- **schema 升级回滚路径**：v2 → v1 兼容（P2 风险低；如果升级后用户回退老 binary，checkpoint 反序列化失败即可，不再加回滚路径）
- **skill_loads 持久化的 retention 策略**：P0 ephemeral，P2 可加 "保留最近 N 个 thread" 避免 db 膨胀
- **stress test 并行化**：目前 N=20 串行（~10 min），未来可加 `--stress-parallel` 提升速度（P1 场景下 10 min 可接受）
- **CI integration**：stress harness + ink-testing 接入 GitHub Actions（需要 secrets 配 910B 凭据 + minimaxi API key；不在 P1）
- **真实 stdout flushing 验证**：TUI 真 stdin 后 spawn 的子进程 stdout 是否真 flush，单独 e2e 测（U1 含部分，但独立可后续补）

---

## Key Technical Decisions

- **ink-testing-library 装为 devDep**：当前 `frontend/package.json` 无 vitest/jest。**P1 装 vitest + ink-testing-library@^4.0.0**（npm 上 2024 发布，零依赖，peerDeps 兼容 Ink 4 + React 18，exports `lastFrame()`）。round 1 round 1 错换为 @testing-library/react（@testing-library/react 在 Ink 4 无 DOM 不可用），round 2 修正。**Alternative Considered**: skip 测试只手动 smoke（P0 阶段就是这么干的）。**Decision**: user 选了"含 TUI stdin 真交互"，必须自动化
- **CheckpointState 加 `version: int` 字段**：当前 CheckpointStore 用 JSON 不分版本，加版本号字段便于迁移。新字段 `skill_loads: list[SkillLoad]`（dataclass 序列化走 `to_dict`）；`from_checkpoint` 读 `version=1` 时降级（无 skill_loads），`version=2` 走全字段。**Alternative**: 用 `pydantic` schema 迁移（P2 太重）
- **stress harness = `e2e_real_op.py --stress N`**：复用现有 e2e 入口，加 `--stress 20` 模式循环 N 次、累计成功 / stderr 摘要。**Alternative**: 新建 `tests/hardware/stress_910b.py` 独立脚本（P1 复用避免重复；`scripts/` 是给用户跑的入口）
- **stress 指标 = 累计 pass rate ≥95%**：单次 spike 10/10 没意义；N=20 累计 ≥19/20 是用户可接受门槛（5% 失败 = 1 次空跑，warn 但不阻塞）
- **ink-testing-library 集成测用 FakeExecutor**：`useBackendProcess` 启动 backend 子进程但 fake `_setup_agent` 里的 NpuExecutor（避免测试 910B）；通过 monkey-patching 注入 fake agent + 验证 TUI screen 输出
- **TUI 测试范围聚焦在 screen assertion**：不测打字速度、不测颜色（ink 渲染层），只断言 `lastFrame()` 含特定文本（"loaded:" / "completed" / "请确认" 等）

---

## High-Level Technical Design

### 数据流（真交互）

```
TUI 端(CLI 进程)
   │  stdin 写 JSON-RPC {"method":"agent.run","params":{"user_input":"op: 实现 add 算子"}}
   ▼
backend 子进程 (spawn 'python -m ascend_op_agent.backend')
   │  _setup_agent() → _orchestrator 实例化(build_new_dev_graph + NpuExecutor + agent_factory)
   │  _handle_run_conversation("op: ...") → 调用 _orchestrator.invoke(...)
   │  phase_callback → send_notification("agent.progress", ...)
   │  done 时 → _push_skill_usage_to_frontend(thread_id)
   ▼
stdout JSON-RPC {"method":"agent.progress","params":{"phase":"design","event":"started"}}
   │
backend 子进程 → backend 子进程 stdout = TUI 端 parent.stdout
   │  parent.stdout.on('data', ...) parse JSON → setMessages / setProgress
   ▼
ink-testing-library assert lastFrame() contains "loaded:" + 完成时 "对话完成"
```

### schema v1 → v2 迁移

```python
# v1 (P0)
@dataclass
class Checkpoint:
    thread_id: str
    state_json: str  # serialized OpState
    # version: int  ← 之前没有

# v2 (P1)
@dataclass
class Checkpoint:
    thread_id: str
    state_json: str
    version: int = 2
    skill_loads_json: str = "[]"  # 新增;v1 checkpoint 迁移时为 "[]"

# 反序列化迁移
def load_checkpoint(db_path, thread_id):
    row = db.execute("SELECT state_json, version, skill_loads_json FROM checkpoints WHERE thread_id=?", (thread_id,))
    state = json.loads(row[0])
    if row[1] < 2:
        # v1 → v2 迁移:skill_loads 缺省空
        state["skill_loads"] = []
        save_checkpoint(db_path, thread_id, state, version=2, skill_loads=[])  # 写回 v2
    else:
        state["skill_loads"] = json.loads(row[2])
    return state
```

### stress N=20 流程

```
for run in 1..N:
    result = run_e2e_real_op_once()  # 现有 reference migration 流程
    if result.success:
        pass_count += 1
    else:
        fail_count += 1
        log_stderr_summary(run, result.stderr[:500])

pass_rate = pass_count / N
if pass_rate < 0.95:
    print(f"FAIL rate {1-pass_rate:.0%} > 5%; see failures above")
    sys.exit(1)
print(f"PASS rate {pass_rate:.0%} ({pass_count}/{N})")
```

---

## Implementation Units

### U1. CheckpointState schema v2 + 老 v1 迁移

**Goal**: CheckpointStore 加 `version: int` + `skill_loads_json: str` 字段；v1 老 checkpoint 自动迁到 v2；serialization round-trip 无损

**Requirements**: R2

**Dependencies**: 现有 `CheckpointStore`（`orchestrator/checkpoint.py`）+ `SkillLoad` dataclass（已有，在 `cannbot_loader.py`）

**Files**:
- Modify: `src/ascend_op_agent/orchestrator/checkpoint.py`（DB schema 升级 + load/save 加 version 字段 + migration 函数）
- Modify: `tests/unit/orchestrator/test_checkpoint.py`（加 v1→v2 迁移测试 + round-trip test，含 edge case: 缺字段 / 损坏 JSON / 跨版本）
- Modify: `src/ascend_op_agent/config.py`（CheckpointConfig 加 schema_version 字段，默认 2，可手动 override 强制 v1 读取）

**Approach**:
- **DB schema: SQLite generated column**（round 2 决策：避免双写 drift）：
  - `checkpoints` 表加 `skill_loads_json TEXT GENERATED ALWAYS AS (json_extract(state_json, '$.skill_loads')) STORED`（SQLite 3.46+；single source of truth = state_json；skill_loads_json 是计算列，零 drift 风险）
  - `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000` + `BEGIN IMMEDIATE` 防 SQLITE_BUSY（macOS/Linux 跨平台，无需 OS 文件锁）
  - 启动期 ALTER TABLE ADD COLUMN（atomic migration），不在 lazy on-read
- **拆 read 与 migrate + payload 验证**（round 2 决策：避免 read+write race + silent downgrade）：
  - `read_checkpoint(thread_id)` — 纯读，无副作用；同时检 row.version + payload schema（mismatch 抛 `CheckpointCorruptError`）
  - `load_and_migrate_checkpoint(thread_id)` — 显式 migrate 入口，调用方决定何时跑
  - `save_checkpoint(...)` 实例方法 — 接受已 migrate 的 v2 state，写入 state_json（skill_loads_json 是计算列自动同步）
  - `CheckpointCorruptError` 抛出时把坏 row 复制到 `.quarantine/{thread_id}-{ts}.json` + ERROR 日志
- 序列化: `to_checkpoint` 写 `{state, skill_loads: [SkillLoad.to_dict()], version: 2}`（只写 state_json，skill_loads_json 由生成列派生）
- 测试: `test_save_v2_loads_skill_loads`, `test_load_v1_migrates_to_v2_on_access`, `test_round_trip_v2_preserves_skill_loads`, `test_corrupt_json_raises_checkpoint_corrupt_error_with_quarantine`, `test_10_process_concurrent_migrate_no_data_loss`, `test_read_checkpoint_no_side_effect`, `test_payload_version_mismatch_raises_corrupt_error`

**Patterns to follow**: `CheckpointStore.from_config`（现有 lazy init）+ `test_checkpoint.py` 现有测试模式（用 tmp_path）

**Test scenarios**:
- Happy: v2 checkpoint save → load → `state["skill_loads"]` 还原（含 2 条 SkillLoad）
- Migration: v1 checkpoint 写盘（手工制造无 version 列的 JSON）→ load → 自动 v1→v2 → skill_loads 缺省 []
- Round-trip: save → load → 数据 hash 一致
- Edge: 损坏的 `skill_loads_json` JSON → load 返 `[]` + log warn (`was_corrupt=True` 字段标记)，不 crash
- Multi-thread: N=100 thread × save/load 并发 → 无 race condition（SQLite WAL 模式）
- Corrupt migration: v1 JSON 字段类型错（state_json 是 list 不是 dict）→ migrate 返 failure 不 crash

**Verification**: `pytest tests/unit/orchestrator/test_checkpoint.py -q` 5+ 测试全过；手工造 v1 老 checkpoint (无 version 列) 用 load → 写入 v2 不丢 state

---

### U2. ink-testing-library 集成 + 真实 stdin RPC 端到端

**Goal**: TUI 真 spawn backend → stdin JSON-RPC 发 `agent.run` → 收 `agent.progress` → screen 渲染真更新；用 ink-testing-library 自动化

**Requirements**: R1, R4

**Dependencies**: U1（skill_loads 持久化到 checkpoint 后可断言屏幕有 chip）

**Files**:
- Modify: `frontend/package.json`（devDep 加 `vitest@^2`、`ink-testing-library@^4.0.0`、`@testing-library/react@^14` + `react-dom@^18.2.0` + `@types/react-dom@^18.2.0` + `jsdom@^24`；test script `vitest run`）
- Create: `frontend/vitest.config.ts`（vitest 配置：environment='jsdom' + ink-testing-library setup）
- Modify: `frontend/src/App.tsx`（加 `data-testid` 属性给 ProgressBar / skill chips / completed message，便于 ink-testing `lastFrame()` 断言）
- Create: `frontend/src/__tests__/run-conversation.test.tsx`（vitest 测试，spawn backend + FakeExecutor + 验证 RPC flow）
- Create: `tests/integration/test_tui_stdin_real.py`（Python 端: subprocess spawn npm test → 验证 frontend 完成 RPC）

**Approach**:
- `useBackendProcess` 已存在（observation 4856），但 unit 测试时用 mock 子进程；`run-conversation.test.tsx` 反向 — spawn **真** backend（`python -m ascend_op_agent.backend`）但 mock 内部 LLM/910B
- 后端 mock 通过 env var `ASCEND_OP_AGENT_CONFIG=mock_config.yaml`（mock LLM API + 无 SSH），backend 进程能跑通 stdin JSON-RPC
- **stdin flush**：`PYTHONUNBUFFERED=1` 在 spawn 时设（避免 backend 进程 stdio 缓冲卡住 TUI 端）
- **EOF 检测**：App.tsx 在 `useBackendProcess` 返回 EOF 时显式 `setState('error')` 并在屏幕显示 "后端连接断开"（避免 hang）
- @testing-library/react `render(<App/>)` → `lastFrame()` 含 plain text 断言（Ink 4 无 DOM，`data-testid` 不可靠；用 `screen.getByText('对话完成')` 文本匹配更稳）
- multi-frame: `rerender` 多次 + `waitFor` 助手捕获异步更新（skill.usage push 后 chip 出现）

**Patterns to follow**: 现有 `tests/integration/test_tui_e2e.py` 的结构（看 frontend structure，但写真正的 RPC）；现有 `useRPC.ts` 的 JSON 解析；观测 5047（agent.progress 通知）已确认 contract

**Test scenarios**:
- Happy: app.run "op: 实现 add 算子" → 后端真 spawn → stdin 发 RPC → wait for `agent.progress` → lastFrame 含 "loaded:" chip
- Interrupted (HITL): 后端 `pending_confirmation` 弹出 → screen 显示 "请确认"
- Error: 后端 inject 异常 → screen 显示 `[ERROR] <msg>` 不挂死
- Recovery: `session.resume_with_input({"approved": True})` → screen 从 "请确认" 变 "对话完成"
- Skill chip: 跑完查 `lastFrame()` 含 "loaded: cuda2ascend-simt" 文本
- EOF: 杀掉 backend 子进程 → screen 显示 "后端连接断开" 不 hang（~3s timeout）

**Verification**: `cd frontend && npm test` → vitest 跑通；output 含 "5 passed" 或类似；同时 `tests/integration/test_tui_stdin_real.py` exit 0

---

### U3. e2e_real_op.py 加 stress 模式 N=20 累计 ≥95% 通过

**Goal**: 一键跑 N=20 reference migration，统计累计 pass rate，<95% 即 exit 1（不阻塞但明确）

**Requirements**: R3

**Dependencies**: 现有 `scripts/e2e_real_op.py`（已实现 reference migration，含真实 910B + 真实 LLM）

**Files**:
- Modify: `scripts/e2e_real_op.py`（加 `--stress N` arg + 累计 pass rate 计算 + stderr 摘要日志 + exit code）
- Create: `scripts/run_stress.sh`（薄包装：`./scripts/run_stress.sh 20` → python e2e_real_op.py --stress 20）
- Modify: `tests/integration/test_e2e_stress_arg.py`（argparse 单测 + 累计逻辑 mock 测试，不真跑 910B）

**Approach**:
- `--stress N` 默认 None（保持原 e2e 行为）；指定时进入循环模式
- 每次跑前 `--thread-id $(hostname)-$$-$(date +%s%N)-$run` 避免 thread 冲突（hostname+pid+纳秒时间，CI 多 worker 不撞；废弃 XSTRESS_RUN_ID env var）
- success 判定: `state["compile_result"]["success"] is True AND state["precision_report"]["success"] is True`（precision_report 来自 U1 run_st_driver 真实跑 910B）
- **retry 策略**（F10）：失败重试 1 次（防 SSH transient），但 report 拆双指标：
  - `first_try_pass_rate = first_try_pass / N`
  - `final_pass_rate_with_retry = final_pass / N`
  - ship criterion: `first_try_pass_rate >= 80%` AND `final_pass_rate_with_retry >= 95%`
- 失败摘要: `tail -500 <(compile stderr) + <(precision stderr)` 写到 `$E2E_STRESS_LOG/<run>.log`
- 循环结尾: 显式打印 `PASS rate X% (first-try) Y% (with-retry) (N/M)` + 三态 exit code:
  - `first_try_pass_rate >= 80%` AND `final >= 95%` AND `first_try == final` → exit 0 (clean pass)
  - `first_try_pass_rate >= 80%` AND `final >= 95%` AND `first_try < final` → exit 0 + stderr WARN line (transient recover)
  - 否则 → exit 1 (real failure)

**Test scenarios**:
- Argparse: `--stress 5` parsed correctly
- Mock N=20 with 19 pass + 1 fail → pass_rate=95% → exactly threshold (不 fail)
- Mock N=20 with 18 pass + 2 fail → pass_rate=90% → fail & exit 1
- Pass rate 100% → exit 0 silently
- Stderr 摘要日志存在

**Verification**: `tests/integration/test_e2e_stress_arg.py` 全过；手工 `--stress 3` 跑（无需 910B，单次即快速）；真 N=20 跑（~10 min，910B）

---

### U4. CLI 真 stdin RPC 端到端（spawn backend → 完整链路）

**Goal**: `scripts/e2e_tui_real.py` — 启动 frontend/dev，spawn 真 backend 子进程，模拟用户输入 `op: 实现 add 算子` 到 backend stdin，监听 stdout → agent.progress → 断言屏幕 + 检查 chip 显示 + 最后 task_done

**Requirements**: R1 + R4 集成验证

**Dependencies**: U2（ink-testing-library 装好）+ U3（stress harness 跑通）

**Files**:
- Create: `scripts/e2e_tui_real.py`（启 frontend dev + 触发 backend RPC + 断言；**stdin flush 防 hang**：spawn env `PYTHONUNBUFFERED=1` + backend `--unbuffered` flag；用 pexpect 或 select+timeout 30s 读 stdout；EOF detector 触发 `setState('error')`）
- Modify: `tests/integration/test_e2e_tui_real.py`（Python wrapper）

**Approach**:
- **双路验证**（F11 decision）：
  - **Fast smoke 30s**（pytest 默认 timeout）：FakeExecutor 路径，验证 U2 R1+R4 端到端 + skill chip 数据流
  - **Real 60s+ stress**：调 U3（e2e_real_op.py --stress N），~10min 跑 N=20 累计 ≥95%
- 在 conda 环境下 spawn `npm run dev`（开发模式 frontend） + 独立 spawn `python -m ascend_op_agent.backend`（模拟 minimaxi API 时用 mock config）
- Pipe 模式：用 pexpect 或 pty 模拟 stdin（PYTHONUNBUFFERED=1 已设，pytest-timeout ≥60s）
- 截屏：通过 frontend 暴露的 test endpoint（vitest dev server 的 API），或读 backend stdout JSON-RPC 直接断言（不真进入 UI，节省复杂度）
- 6-7 个 fixture 脚本一次性跑（happy / HITL / error / skill chip），run 完输出 P1 验收报告

**Patterns to follow**: 现有 `tests/integration/test_e2e_remote.py`（如果存在）+ `tests/integration/test_backend_resume.py`

**Test scenarios**:
- Backend spawn failure → exit 1 with diagnostic
- Backend spawn success + agent.run OK → exit 0 + JSON-RPC log ≥ 1 progress event
- Frontend dev server build OK + vitest run produces 5+ tests pass
- E2E 真交互: send "op: 实现 add" → wait status_completed ≤ 30s (fast) OR ≤10min (real) → assert

**Verification**: `python scripts/e2e_tui_real.py` exit 0 + 输出 backend spawn time + RPC roundtrip count + frontend build success + U2 vitest pass count ≥ 5 + U3 stress N=20 ≥95% (双指标: first-try ≥80%, with-retry ≥95%)

---

## System-Wide Impact

- **Interaction graph**: 用户跑 `ascend-op-agent run "op: 实现 add"` → 进程正确 spawn → RPC 流不带 leak；进程退出时 stdin/stdout fd 都 close
- **State lifecycle**: `state["skill_loads"]` 跨 run 持久（U1），跨进程重启仍可见（schema v2 升级到 sqlite）；不再依赖 ephemeral registry
- **Error propagation**: backend 进程 crash → TUI 立刻收到 EOF → screen 显示 "后端连接断开" 而不是 hang
- **API surface parity**: `~/.ascend_op_agent/checkpoints.db` 的 schema 加 version 列，老 checkpoint 自动 v1→v2 迁移，向后兼容（P0 老用户升级不丢数据）
- **Integration coverage**: ink-testing TUI 测试 + subprocess backend 真实交互 + 910B stress，三层都覆盖（之前 U7 只覆盖 mock 链路）
- **Unchanged invariants**: A 现有 mock backend RPC 测试 + B 10/10 spike 数据 + C schema v1 read compat + D 老用户 v1 db 自动升级

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| ink-testing-library 与 React 18 + Ink 4 兼容性问题 | Spike 先装 run 最小 hello world；失败回退手测（但 plan 是 user 选了 TUI 真交互 = 必须自动化，备选只有忍） |
| 910B N=20 stress 期间 SSH/容器抖动 | 失败 case 自动重试 1 次（避免 transient 误计失败）；仍 fail 才计入 fail_count |
| backend subprocess stdin/stdout 没 flush 导致测试 hang | pytest 加 `timeout=30`（每 case 最多 30s，超时即 fail 标记），不 hang 整个 suite |
| CheckpointState schema 升级后老 db 破坏 | migration 显式触发（lazy on first read），不一次性整表 ALTER（SQLite-safe per-row migration） |
| stress N=20 串行 ~10min 在 CI 太长 | 默认 N=5（~2.5 min）作为 CI smoke；`--stress 20` 用户手动跑 |

| Dependency | Notes |
|------------|-------|
| 910B msOpUT 路径 | U0.5 已验证（10/10 PASS 单发） |
| minimaxi MiniMax-M3 API key | `~/.ascend_op_agent/.env` 已有，U7/U8 用过 |
| ink-testing-library React 18 兼容 | `peerDependencies` 检查 install 时报错 |
| vitest ink plugin | 需 `vitest@^2 + @testing-library/react@^14` |

## Phased Delivery

```
Week1: U1 CheckpointState schema v2（最独立,纯 Python,小改）+ U3 stress 模式（同样纯脚本,2-3 天）
Week2: U2 ink-testing-library 集成（前端依赖装 + 测试写,3-5 天,前端首次有测试）
Week3: U4 CLI 真 stdin RPC 端到端（依赖 U2 + U3,通过 e2e_tui_real.py 整链路验证）
Week4: 跑全套 N=20 stress + U2 + U4 终验 + 验收报告

U1 → U3 并行（独立）,U2 单独（前端新依赖）,U4 依赖 U1+U2+U3。
```

---

## Sources & References

- **Origin plan**: [docs/plans/2026-06-26-002-feat-post-p0-completeness-robustness-plan.md](2026-06-26-002-feat-post-p0-completeness-robustness-plan.md)（P0 completed, 5 gap verified）
- **Strategy**: [STRATEGY.md](../../STRATEGY.md)（tracks: Persistent Memory / Tool Discovery / Workflow Orchestration / Skill Crystallization）
- **P0 e2e 报告**: [docs/e2e/2026-06-28-e2e-5gap-report.md](../e2e/2026-06-28-e2e-5gap-report.md)（5 gap all satisfied, 但均为 mock 级别）
- **observation 4856**: Frontend-backend IPC via child_process stdio pipes（useBackendProcess spawn 模式）
- **observation 6306**: Agent progress notification feature merged to develop（agent.progress contract）

