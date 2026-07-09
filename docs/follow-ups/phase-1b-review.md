# Phase-1b Code Review Findings — Follow-Ups

**Review**: code-review xhigh(10 angles × 8 candidates → 1-vote verify → 15 findings)
**Date**: 2026-07-09
**Branch**: `feat/task-mgmt-phase-1b`(待 R16 gate 后决定 merge / abandon)
**已修**: #4 calibrate 无 LLM 警告、#10 删 dead `--classifier-fixture` flag(commit `78a5333`)
**本文档**: 剩余 13 个 latent findings 的处置计划(merge 前 / dogfood 期 / follow-up plan)

处置分三档:
- **🔴 merge 前修**:可达或近可达 correctness bug,merge 前应修
- **🟡 dogfood 期观察**:需要真实流量/数据才知是否触发,dogfood 期间监控
- **🟢 follow-up plan**:altitude/重构/并发场景,二期-b follow-up plan 处理

---

## 🔴 merge 前修(3)

### #1. `commands.py:179-181` suggest() 无条件覆盖已 wiring 的 RelationBuilder.llm_call
- **现状**:注释"不覆盖已 wiring 的 builder"与代码 `builder.llm_call = llm_call`(无条件)矛盾
- **触发**:production pre-wire RelationBuilder + 调 `suggest(llm_call=X)` → 共享 builder 被改成 X,后续 production 调用静默用 X
- **当前可达性**:latent(CLI 不 pre-wire,backend 未集成);但注释矛盾 = 真bug,1 行能修
- **修法**:`suggest` 不 mutate 注入的 builder,改为构造临时 builder 或让 llm_call 只作用于本次调用(传参给 `builder.suggest`)
- **3 angles 共指**(A/C/E)

### #11. `artifacts.py:252` MockArtifactWriter forward 路径丢 written_by
- **现状**:`MockArtifactWriter(forward_store=store).__call__` forward 时默认 `WRITTEN_BY_MANUAL`,但 mock 自身记录 `WRITTEN_BY_EXECUTOR` → mock 与 store 状态不一致
- **触发**:test 用 mock + forward_store,下游按 written_by 过滤见不一致
- **当前可达性**:test-only,但影响 U8 writer hook audit 测试可信度
- **修法**:forward 路径透传 written_by(构造参或默认 WRITTEN_BY_EXECUTOR)

### #6. `intent_classifier.py:285` + `relation_builder.py:146` 贪婪正则误解析多 JSON 块
- **现状**:`re.search(r"\{[\s\S]*\}", raw)` 贪婪,LLM 输出 chain-of-thought + JSON 时匹配首个 `{` 到末个 `}` 含中间文本 → json.loads raise → fallback
- **触发**:CoT 类 LLM 输出多 JSON 块;当前 prompt 要求纯 JSON(边缘)
- **修法**:非贪婪 `\{[\s\S]*?\}` 或平衡括号匹配;两处 `_parse` 抽共享 helper(同时解 #reuse-duplication)
- **2 angles 共指**(D correctness + F/G reuse)

---

## 🟡 dogfood 期观察(3)

### #2. `executor_dispatch.py:176` writer hook 失败无回滚
- **现状**:`_register_artifacts_at_boundary` 在 `link_thread` + `invoke` 之后调用,write 抛错 → dispatch 失败但 thread 已 link + checkpoint 已写
- **触发**:PhaseRunner 节点产 artifact 后(当前 None-safe no-op 不触发)+ artifact_store.write 抛错(SQLite busy / 磁盘满)
- **dogfood 监控**:一期-b dogfood 期间 develop 走真 PhaseRunner,若节点开始产 artifact 且 write 失败,task progress 显示 done 但 read_related_artifacts 返空
- **修法(follow-up)**:try/except 包 write,失败 log + 不 abort dispatch(artifact 注册是 best-effort,不应让成功的 invoke 变失败);或 link 前置已做,加 invoke 失败时 unlink 补偿
- **3 angles 共指**(A/C/D)

### #12. `commands.py:64` TaskCommands.__init__ 默认注入 RelationStore → 每次 CLI 调用 mutate tasks.db schema
- **现状**:`__init__` 构 `RelationStore(store.db_path)` → 开 db + `CREATE TABLE IF NOT EXISTS task_relations`
- **触发**:`task list --db /tmp/inspect.db`(只读/借来文件)→ 写 schema → PermissionError 或污染证据文件
- **dogfood 监控**:用户若拿 tasks.db 做只读检查会踩
- **修法**:lazy-init RelationStore(首次用 relation 功能时才构),或 `__init__` 不开 db

### #5. `executor_dispatch.py:286` _extract_artifacts_from_state 接受空字符串 type/path
- **现状**:dict/list 分支 `isinstance(str)` 过,空字符串 `''` 也过 → 写入 PK `(task_id, '')` 垃圾行
- **触发**:PhaseRunner 节点产空 artifact(当前不产)
- **dogfood 监控**:同 #2,节点产 artifact 后
- **修法**:加 `if not artifact_type or not path: skip` guard

---

## 🟢 follow-up plan(7)

### #3. `intent_classifier.py:271` ThreadPoolExecutor timeout 泄漏线程/socket
- **现状**:每次 classify 新建 max_workers=1 pool,timeout 后 `shutdown(wait=False)` → worker 线程 + HTTP socket 继续跑
- **触发**:LLM client hang + 持续 classify → 线程/FD 累积 → "cannot create new thread"
- **修法**:`__init__` 复用单实例 daemon executor,进程退出 close()
- **3 angles 共指**(A/D/H);follow-up 因当前 LLM 未 wire(default explicit-only)

### #8. `artifacts.py:167` write() 非原子 commit + 读回
- **现状**:INSERT commit 后用第二个连接 `_get_row` 读回 → 并发写者可覆盖间返错误数据
- **触发**:并发 ArtifactStore.write 同 task 同 path
- **修法**:`INSERT ... RETURNING`(sqlite ≥3.35)单连接
- **follow-up 因**:当前无并发写 artifact 路径

### #9. `relations.py:214` cycle 检测 TOCTOU(事务外)
- **现状**:`_would_create_cycle` 跑在 BEGIN IMMEDIATE 外,两并发写者各过检查后 INSERT 出环
- **触发**:并发 add_relation(未来 PhaseRunner ThreadPoolExecutor 并发 dispatch)
- **修法**:cycle 检测 + INSERT 同一事务/c连接;或 SERIALIZABLE 隔离
- **3 angles 共指**(A/C/D);follow-up 因 CLI 单进程安全

### #7. `relations.py:165` edit_relation 无法修复既存自环
- **现状**:`_would_create_cycle` 对 `src==dst` 无条件返 True(无视 exclude_edge)→ edit 自环边总 raise
- **触发**:既存自环边(当前 add_relation 有 self-loop guard,不会有;legacy 数据才触发)
- **修法**:`src==dst 且 exclude_edge==(src,dst)` 时当 no-op

### #13. `intent_classifier.py:250` disambiguate=True 静默改 label + half-built 接口
- **现状**:`disambiguate=True` 时 label 被改成 NEW_TASK;全仓无 consumer 读 `ClassificationResult.disambiguate`
- **触发**:backend/chat 集成时若只看 label → 静默开空 new-task,disambig prompt 永不送达
- **修法**:disambiguate 时 label 清空/置 sentinel;或第 5 label + 强制 handler
- **follow-up 因**:依赖 backend 集成(dogfood 后)

### #14. `executor_dispatch.py:260` _extract_artifacts_from_state 3 形态 fragile bandaid
- **现状**:宽容解析 dict / list-dict / list-tuple + 静默 debug-drop;PhaseRunner apply_update last-write-wins,节点切形态被无声覆盖
- **修法**:PhaseRunner 显式声明 artifact schema(收敛 `dict[str,str]`)+ OpState TypedDict 受控字段
- **3 angles 共指**(B/D/I);altitude,二期-b 重构

### #15. `executor_dispatch.py:56` Path A gate 文件存在 marker 过浅
- **现状**:`~/.ascend_op_agent/path_a.spike_passed` 文件存在 = gate 过;无 content schema/版本/stale 检测
- **触发**:touch 文件调试忘删 → migrate 误开;Path A 升级后旧标志仍在但契约已变
- **修法**:marker 文件写 JSON `{passed_at, version, executor}` + json.load 校验;或纳入 Config pydantic 体系
- **follow-up 因**:Path A 本身 delivery-gated,未 plan-done

---

## 处置汇总

| 档 | 数 | findings | 时机 |
|----|----|---------|------|
| 已修 | 2 | #4 #10 | commit `78a5333` |
| 🔴 merge 前 | 3 | #1 #11 #6 | R16 GO 后、merge 到 develop 前 |
| 🟡 dogfood 观察 | 3 | #2 #12 #5 | 二期-b dogfood 期监控 |
| 🟢 follow-up plan | 7 | #3 #8 #9 #7 #13 #14 #15 | 二期-b follow-up plan / 重构 |

**merge gate**:R16 GO → 先修 🔴 3 个 → merge `feat/task-mgmt-phase-1b` 到 develop。
**abandon**:R16 NO-GO → 删 branch(本文档留作 finding 记录)。