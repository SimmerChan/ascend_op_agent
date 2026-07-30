---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-plan-bootstrap
title: feat: 真实 ST 驱动精度验证闭环（op_add 端到端）
created: 2026-07-29
deepened: 2026-07-29
---

# feat: 真实 ST 驱动精度验证闭环

## Goal Capsule

让 `scripts/e2e_real_op.py` 的 `precision_report` 从 `total: 0, passed: 0, failed: 0` 变成 **`total: 10, passed: 10, failed: 0`**（FP32 6 个 + INT32 4 个），跑真 910B NPU 算子 → CPU golden → MERE/MARE / 元素一致 对比。完成单算子端到端交付最后 1 个占位环节。当前所有前置已闭环（compile success=True, 06f4a33）。

## Problem Frame

2026-07-29 06f4a33 commit 后 e2e_real_op 跑 op_add：analyze → codegen → review_fix → compile_fix_loop（1 轮自愈）→ **compile success=True** → precision（**total: 0**）→ delivery → done。精度验证节点未真跑，是项目最后 1 个占位。

**根因**（多重叠加，由 ce-doc-review 4 reviewers 实测验证）：

1. **ST 二进制编译/链接缺 op_api/ 符号**：`tests/st/CMakeLists.txt` 引用 `aclnn_op_add.h` + 链接 `test_aclnn_op_add`，但 `op_api/` 子目录在 loader 生成工程里**不存在**。预期错误信号：cmake 阶段 `fatal error: aclnn_op_add.h: No such file or directory`，或链接阶段 `undefined reference to aclnnOpAddGetWorkspaceSize`。两种 error class 都触发 `_run_st_driver_recipe` 失败 → `_parse_st_stdout` 落降级 `total=0`。
2. **loader `_strip_opapi_section` 阻断**：`cannbot_loader.py:670` 在读 vendor `op_host/CMakeLists.txt` 时无条件调用 `_strip_opapi_section` 删除 `cust_opapi` library 段。这是 d725530 / 10005 commit 的"方向 B 不写 op_api/"决策。但现在 op_api/ 恢复后，loader 必须**保留** `cust_opapi` 段（含 op_api_srcs + package_add）。
3. **vendor op_api/ 文件结构**：实际是 4 文件 `aclnn_add_example.{h,cpp}` + `add_example.{h,cpp}`（不是 plan 假设的 3 文件），**无 `op_api/CMakeLists.txt`**。op_api library 构建逻辑全部位于 vendor `op_host/CMakeLists.txt:89-115`（`set(op_api_dir)` + `npu_op_library(cust_opapi ACLNN ${op_api_srcs})`），不需 `add_subdirectory(op_api)`。

**当前矛盾**：d725530 commit 配套删了 `cust_opapi`（避免 LLM 写不出 op_api/ 空目录致 CMake "No SOURCES given to target cust_opapi"）；但 ST 驱动**依赖** `cust_opapi` 链接到 `aclnnOpAddGetWorkspaceSize`。两者不能共存——本次修复要把 op_api/ 用 scaffold 注入方式恢复，与方向 B 兼容（LLM 不写 op_api/ 内容，只靠 vendor 参数化注入）。

## Scope Boundaries

**In scope:**

1. loader 注入 `op_api/`（4 文件参数化到 `{op_snake}/` + `aclnn_{op_snake}/.{h,cpp}`）
2. 移除 `_strip_opapi_section` 调用（保留 vendor `op_host/CMakeLists.txt` 的 `cust_opapi` 段）
3. kernel 入口符号自校验：`op_kernel/{op_snake}_arch22.cpp` 必须导出 `OpPascal` 函数（op_api `l0op::OpPascal` 链接符号）
4. `make_real_precision_node` 透传 `success`/`error`；e2e 报告展示 per-case mere/mare（FP32）/ element_consistent（INT32）
5. 加 ST 失败可观测：`_run_st_driver_recipe` 错误信号（含 stderr 摘要）写 `precision_report.error`，e2e 报告打印

**Out of scope:**

- 改 vendor submodule（任何 `vendor/cannbot-skills/` 文件）—— reviewer 发现改 vendor 跨仓权限 + 共享模板副作用过大，本仓 loader 注入即可
- `precision_fix_loop`（ST 链接失败时无 LLM 自愈）—— **明确 deferred**，本次单 run 闭环靠 error 透传
- 多算子并发（独立 follow-up）
- framework_adapt 真实代码生成（现 HITL sample 占位）
- N=5 stress 跑本次闭环（ship gate 的事）
- LLM 自动产 10 个 test cases（用 vendor hardcode 10 个即可）

## Key Technical Decisions

**KTD-1：op_api 注入方式** — 在本仓 `cannbot_loader.load_build_scaffold` 里**新增** `_OP_API_FILES` 列表（4 文件），参数化文件名（`add_example.{h,cpp}` → `{op_snake}.{h,cpp}`、`aclnn_add_example.{h,cpp}` → `aclnn_{op_snake}.{h,cpp}`）+ 文件内容（替换 `add_example`/`AddExample`）。**不动 vendor**，从 vendor 直接读 4 文件原文 + 参数化。理由：避开 vendor submodule 跨仓权限 + 复用已建立的 ST 文件参数化模式（`_ST_FILES` line 635-639）。

**KTD-2：移除 `_strip_opapi_section` 调用** — 删除 `cannbot_loader.py:668-670` 的 `if rel == "op_host/CMakeLists.txt": content = _strip_opapi_section(content)`。理由：loader 已经注入 op_api 4 文件 → op_host/CMakeLists 必须含 `cust_opapi` 段才能编译这些文件。两步必须同步进行。`_strip_opapi_section` 函数本体保留（注释标 deprecated）—— 不删是避免影响其他调用方（grep 全仓无其他调用，但保留无副作用）。

**KTD-3：kernel 入口符号自校验** — op_api `aclnn_{op}.cpp` 里 `l0op::{OpPascal}(...)` 调用必须匹配 kernel 导出符号。校验方式：load_build_scaffold 注入 op_api 后 + codegen_kernel 节点完成后 + compile 节点前，**新增 U2.5 kernel_symbol_validator 节点**：扫 `code_result.files`，断言 `op_kernel/{op_snake}_arch22.cpp` 含 `__global__ __aicore__ void {OpPascal}(` 或 `__global__ __aicore__ void op_add(`（snake_case 两种命名约定都接受）。**不匹配时 raise fast-fail**，明确告诉 LLM codegen_kernel prompt 哪个符号错了。理由：避免 ST 跑到 link 阶段才报 `undefined reference`，提前到 graph build 阶段就 fail-fast。

**KTD-4：error 透传契约（无 ST 自愈）** — ST 失败时 `_run_st_driver_recipe` 已有 `_st_driver_failure` 返 `{success: False, error: str, total: 0}`。**`make_real_precision_node` 只需透传**（`update["precision_report"] = report`），e2e 报告 `_main_single_report` 加 print `success` / `error` / 前 3 case 摘要。**不引入 precision_fix_loop**（YAGNI；compile_fix_loop 已在 precision 前 finished，无 sequencing 窗口接 ST 错）—— **明确 deferred**。

**KTD-5：test_cases 来源** — 用 vendor `tests/st/test_aclnn_add_example.cpp` 已 hardcode 的 10 个 case（FP32 6 + INT32 4）。loader `_ST_FILES` 已参数化（d725530）。本次不新增 test case 注入。

**KTD-6：R3 数据类型感知契约**（reviewer scope-guardian #3）— `precision_report.cases` 每条 schema：
- FP32 case：`{case_id, passed, metrics: {mere, mare, threshold, elems}}`
- INT32 case：`{case_id, passed, metrics: {elems, dtype: "int"}}` + `passed=True` 当所有 element 相等

R3 改写为：**所有 10 case 都有 `elems` + `passed`**；FP32 case 含 `mere`/`mare`/`threshold`，INT32 case 含 `dtype: "int"` 且 `passed` 语义为"元素全等"。

## Requirements

R1. e2e_real_op 跑 op_add 默认任务，`precision_report.total_cases = 10`
R2. 10 个 ST case 全部 PASS（FP32 MERE < 1.22e-4 AND MARE < 1.22e-3；INT32 所有 element 一致）
R3. `precision_report.cases` 列出每个 case 的 metrics（FP32 含 mere/mare/threshold/elems；INT32 含 dtype=int/elems + passed 语义"元素全等"）
R4. ST 编译/链接失败时 `precision_report.success = False` + `precision_report.error` 字段填 stderr 摘要（不抛）
R5. compile 失败时 precision 节点跳过（保持现有 `compile_not_ready` 守门），不污染 stderr
R6. kernel 入口符号 (`OpPascal` 或 `{op_snake}`) 与 op_api `l0op::` 调用名一致（U2.5 自校验 fast-fail）

## High-Level Technical Design

```
op_add 工程（loader 注入 + LLM 语义）
├── 根 CMakeLists.txt     ← scaffold 注入（参数化 op_snake / OpPascal / npu_op_package 注册 op_host 子目录）
├── build.sh              ← scaffold 注入（包名通配符 fix，d725530）
├── op_api/               ← NEW (U1): scaffold 注入 4 文件
│   ├── aclnn_op_add.h    ← 参数化 vendor aclnn_add_example.h
│   ├── aclnn_op_add.cpp  ← 参数化 vendor aclnn_add_example.cpp（l0op::OpAdd / OpAddGetWorkspaceSize）
│   ├── op_add.h          ← 参数化 vendor add_example.h
│   └── op_add.cpp        ← 参数化 vendor add_example.cpp
├── op_host/              ← scaffold 注入（保 cust_opapi 段，U2 移除 strip）
│   ├── CMakeLists.txt    ← 含 set(op_api_dir) + npu_op_library(cust_opapi ACLNN ...) + package_add(cust_opapi)
│   ├── op_add_def.cpp    ← LLM codegen_host 写
│   ├── op_add_infershape.cpp  ← LLM codegen_host 写
│   └── arch22/op_add_tiling.cpp  ← LLM codegen_host 写
├── op_kernel/            ← scaffold 注入 + LLM 写 arch22
│   ├── CMakeLists.txt
│   ├── op_add_arch22.cpp  ← LLM codegen_kernel 写（必须导出 OpPascal / op_add）
│   └── arch22/op_add.{h, _tiling_data.h, _tiling_key.h}  ← LLM codegen_kernel 写
├── op_graph/             ← scaffold 注入 + LLM 写
└── tests/st/             ← scaffold 注入（参数化 10 test cases，d725530）
    ├── test_aclnn_op_add.cpp  ← 包含 aclnn_op_add.h（来自 op_api/）
    ├── CMakeLists.txt
    └── run.sh
```

**关键 sequencing**（KTD-4 决定）：
```
codegen_scaffold (U1 op_api 注入)
  → codegen_kernel/host/proto (LLM)
  → review_fix
  → [NEW] kernel_symbol_validator (U2.5 fast-fail)
  → compile_fix_loop (compile OK)
  → precision → run_st_driver
      ├─ 成功 → precision_report.success=True, total=10/10
      └─ 失败 → precision_report.success=False, error=stderr[:500]
  → delivery_mode
  → done
```

**op_api 参数化模式**（U1 关键代码路径）：
```python
_OP_API_FILES = (
    "op_api/aclnn_add_example.h",   # → "op_api/aclnn_{op_snake}.h"
    "op_api/aclnn_add_example.cpp", # → "op_api/aclnn_{op_snake}.cpp"
    "op_api/add_example.h",         # → "op_api/{op_snake}.h"
    "op_api/add_example.cpp",       # → "op_api/{op_snake}.cpp"
)
# 文件名参数化：rel.replace("add_example", op_snake)
#   "op_api/aclnn_add_example.h" → "op_api/aclnn_op_add.h"  ✓
#   "op_api/add_example.cpp" → "op_api/op_add.cpp"  ✓
# 内容参数化：与 _BUILD_FILES 一致（line 647-651）
#   add_example_custom → {op_snake}_custom
#   add_example_op_prj → {op_snake}_op_prj
#   AddExample → {op_pascal}
#   add_example → {op_snake}
```

## Implementation Units

### U1. load_build_scaffold 注入 op_api/ 4 文件 + 移除 _strip_opapi_section

- **Goal**：loader 注入 `op_api/{aclnn_,}{op_snake}.{h,cpp}` 4 文件；vendor `op_host/CMakeLists.txt` 的 `cust_opapi` 段保留到生成工程。
- **Files**：
  - modify `src/ascend_op_agent/orchestrator/cannbot_loader.py`
    - 加 `_OP_API_FILES` 元组（line 635-639 同形）
    - 加到 `for rel in _BUILD_FILES + _ST_FILES + _OP_API_FILES:` 循环（line 641）
    - 删/注释 `if rel == "op_host/CMakeLists.txt": content = _strip_opapi_section(content)`（line 668-670）
    - 文件名参数化（line 673）扩展到 `_OP_API_FILES` 同形处理（`rel.replace("add_example", op_snake)` 对 `op_api/aclnn_add_example.cpp` 也 work：→ `op_api/aclnn_op_add.cpp`）
    - `_strip_opapi_section` 函数体保留，docstring 标 `[DEPRECATED U1]`
  - modify `tests/unit/test_build_scaffold.py`
    - 删 `test_op_host_cmakelists_strips_opapi_section`（line 74-86，U2 反转）
    - 加 `test_op_host_cmakelists_includes_opapi`（断言 `cust_opapi` + `npu_op_package_add(... cust_opapi)`）
    - `test_load_build_scaffold_returns_8_files_parameterized` → 改 expected set 为 12 文件（5 构建 + 3 ST + 4 op_api）
    - 加 `test_op_api_files_parameterized_no_residual`（4 文件 content 无 `add_example`/`AddExample`）
    - 加 `test_op_api_key_naming`（key 是 `op_api/aclnn_op_add.{h,cpp}` + `op_api/op_add.{h,cpp}` 不是 vendor 原 `add_example`）
- **Approach**：参考 `_ST_FILES`（line 635-639）的注入模式。新增 `_OP_API_FILES` 同 4 元组结构；遍历时共用 `content = content.replace(...)` 参数化；key 参数化沿用 line 673 `rel.replace("add_example", op_snake)`（对 `op_api/aclnn_add_example.cpp` → `op_api/aclnn_op_add.cpp` 正确）。`_OP_API_GUARD`：vendor `op_api/` 缺失 warn + 跳过（与 `_BUILD_FILES`/`_ST_FILES` 同降级策略，line 643-645）。
- **Test scenarios**：
  - happy：`load_build_scaffold("op_add", "OpAdd")` 返 12 文件，含 `op_api/aclnn_op_add.h` + `op_api/aclnn_op_add.cpp` + `op_api/op_add.h` + `op_api/op_add.cpp`
  - 参数化无残留：4 op_api 文件 content 无 `add_example`/`AddExample`/`l0op::AddExample`
  - 文件名参数化：key 全部以 `op_api/op_add.*` 或 `op_api/aclnn_op_add.*` 形式
  - 路径缺失降级：`monkeypatch` vendor root → 不抛 + 返 8 文件（5+3，无 op_api）
  - op_host CMakeLists：含 `cust_opapi` + `op_api_dir` + `npu_op_package_add(... cust_opapi)`，**不再 strip**
- **Verification**：`test_build_scaffold` 全过 + `_OP_API_GUARD` 单元测试

### U2. U2.5 kernel_symbol_validator 节点（NEW）

- **Goal**：编译前 fast-fail 校验 kernel 入口函数符号与 op_api `l0op::` 调用名一致。
- **Files**：
  - modify `src/ascend_op_agent/orchestrator/graphs/new_dev.py`：`_scaffold_inject_node` 与 codegen 节点之间插入 `kernel_symbol_validator` 节点
  - modify `src/ascend_op_agent/orchestrator/nodes/common.py`（或新建 `validation.py`）：`_validate_kernel_symbol(files, op_snake, op_pascal)` helper
  - modify `src/ascend_op_agent/orchestrator/graphs/new_dev.py` codegen_kernel prompt：加 kernel 入口符号约束（"必须导出 `__global__ __aicore__ void OpPascal(...)` 或 `__global__ __aicore__ void op_snake(...)`，**两种 snake_case 与 PascalCase 都接受**"）
  - add `tests/unit/test_kernel_symbol_validator.py`（mock code_result.files）
- **Approach**：
  - 校验逻辑：扫 `code_result.files` 里 `op_kernel/{op_snake}_arch22.cpp`，正则 `r"__global__\s+__aicore__\s+void\s+(\w+)\s*\("`，提取函数名；候选 op_api `aclnn_{op_snake}.cpp` 里 `l0op::\w+\(` 提取调用名。两组名字必须**相交**（任一匹配即过）。
  - fast-fail 行为：不匹配时 `update["__status__"] = "error"`, `update["last_phase_result"]["error"] = "kernel_symbol_mismatch"` 抛出特定 exception 让 PhaseRunner catch。
  - codegen_kernel prompt 增强：明确要求 LLM 用 `OpPascal` 命名 kernel 入口（"命名约定: `void OpAdd(GM_ADDR x, GM_ADDR y, GM_ADDR z, GM_ADDR workspace, GM_ADDR tiling)` —— 必须与 op_api `l0op::OpAdd` 链接符号匹配"）。
- **Patterns to follow**：参考 `make_llm_node` 的 `no_tools=True` 跳过 tool calling 行为（line 153-154）
- **Test scenarios**：
  - happy：kernel cpp 含 `__global__ __aicore__ void OpAdd(...)` + op_api cpp 含 `l0op::OpAdd(...)` → 校验通过
  - snake_case alternative：kernel 含 `void op_add(...)` + op_api 含 `l0op::OpAdd(...)` → 通过（PascalCase 在 op_api）
  - mismatch：kernel 含 `void SomeKernel(...)` 但 op_api 含 `l0op::OpAdd(...)` → fast-fail 抛错
  - missing kernel file：`code_result.files` 无 `op_kernel/op_add_arch22.cpp` → fast-fail
  - missing op_api file：同样 fast-fail
- **Verification**：`test_kernel_symbol_validator` 全过

### U3. make_real_precision_node 透传 + e2e 报告增强

- **Goal**：`precision_report` 完整透传 `run_st_driver` 返回 dict（success/error/cases 都含）；e2e `_main_single_report` 展示 per-case metrics；不动 `make_real_precision_node` 主逻辑。
- **Files**：
  - modify `src/ascend_op_agent/orchestrator/nodes/validation.py`（`make_real_precision_node` 已有 line 104-105 `return {"precision_report": report}` —— **不动**，只是**确认**这就是正确路径；如有需要仅加 `success` 字段透传 patch）
  - modify `scripts/e2e_real_op.py` `_main_single_report`（line 648-652）：扩展 print 块 —— `success` + `error[:200]` + 前 3 case 摘要
  - modify `scripts/e2e_real_op.py`（`make_test_cases_resolver` 函数标 deprecation note："ST driver self-supplies cases via run_st_driver"）
- **Approach**：
  - validation.py 现状已透传 report（line 104-105）：`return {"precision_report": report}` —— 不需要改。**仅当**实际测试发现 success 字段丢失时才打 patch（plan-time 已读 line 104-105 现状 OK）
  - e2e 报告：在现有 print block（line 648-652）后追加：
    ```
    if pr.get("success") is False:
        print(f"  error:     {(pr.get('error') or '')[:200]}")
    for case in (pr.get("cases") or [])[:3]:
        m = case.get("metrics", {})
        print(f"  case {case['case_id']}: passed={case.get('passed')} metrics={m}")
    ```
  - 保留 `make_test_cases_resolver` 占位（line 303-313），加 deprecation comment
- **Test scenarios**：
  - happy：mock executor 返 `{"success": True, "total_cases": 10, "passed_cases": 10, "cases": [10 entries]}` → e2e report 输出含 "passed 10" + 前 3 case metrics
  - failure：mock executor 返 `{"success": False, "error": "...", "total_cases": 0}` → e2e report 输出 "error: ..."
  - 类型感知：FP32 case `metrics` 含 mere/mare/threshold/elems；INT32 case `metrics` 含 dtype/elems（KTD-6 验证 schema）
- **Verification**：`test_validation.py`（新建 4 个 mock 测试）+ e2e 真跑 output 含 10/10 + cases

### U4. U1-U3 集成 + 910B 真 e2e 验证

- **Goal**：跑 `scripts/e2e_real_op.py` 单次 op_add，`precision_report.total_cases = 10`、passed_cases = 10、success = True。
- **Files**：only 跑流程，无新代码改动。
- **Approach**：
  - 跑 `PYTHONPATH=src python scripts/e2e_real_op.py`（默认任务，op_add 单次）
  - 验 stdout：`compile_result.success=True` + `precision_report.success=True total=10 passed=10 failed=0` + 前 3 case 详情
  - 若失败 debug 路径：
    1. ST 编译错（aclnn_op_add.h not found）→ U1 没注入成功（查 `_OP_API_FILES` 列表）
    2. ST 链接错（undefined reference to aclnnOpAdd）→ U1 注入成功但 `cust_opapi` 没注册（查 `_strip_opapi_section` 是否删干净）
    3. 链接错（undefined reference to OpAdd 或 op_add）→ U2.5 没拦住 LLM 写错符号（看 prompt 是否生效）
    4. 运行时错（NPU 上 symbol mismatch）→ op_api 里 `l0op::OpAdd` 实际调用 kernel 不同名
- **Test scenarios**：
  - 全绿：910B 真跑 op_add → compile success + precision 10/10 PASS + done
  - e2e 用时：单次 ~3 min
- **Verification**：e2e stdout 含 `precision_report.success: True` + `passed: 10/10`

## Dependencies

- U1 → U2（U2.5 校验依赖 U1 注入 op_api/ 文件已存在）
- U1 → U3（U3 报告增强依赖 U1 跑通后才有真数据）
- U2 → U4（U4 集成依赖 U2 fast-fail 不误杀）
- U3 → U4（U4 集成依赖 U3 报告格式稳定）

## Verification Contract

| Gate | Command | Pass |
|------|---------|------|
| 新单测 | `HF_HUB_OFFLINE=1 PYTHONPATH=src python -m pytest tests/unit/test_build_scaffold.py tests/unit/test_codegen_subdir.py tests/unit/test_kernel_symbol_validator.py -v` | 全过（12 文件断言 + op_api 注入 + symbol validator） |
| ship gate | `PYTHONPATH=src python scripts/ship_ready.py --only unit_test` | 全过（lint + unit_test） |
| 910B 真 e2e | `PYTHONPATH=src python scripts/e2e_real_op.py` | compile success + precision 10/10 PASS |

## Definition of Done

- 910B 真 e2e 单跑 op_add 默认任务：
  - 14 阶段流水线全过（entry/analyze/design/codegen_scaffold/codegen_kernel/host/proto/review_fix/**kernel_symbol_validator**/compile_fix_loop/**precision**/delivery/framework_adapt/done）
  - `compile_result.success = True, return_code = 0`
  - `precision_report.success = True, total_cases = 10, passed_cases = 10, failed_cases = 0`
  - 10 case 含正确 metrics（FP32 mere/mare，INT32 dtype=int）
- 全部单测过（U1 改 3 文件 + U2.5 改 2 文件 + U3 改 1 文件 + 新测试）
- ship gate 过（lint + unit_test）
- commit + push + plan doc 闭环
- **vendor submodule 完全不动**（避开跨仓权限风险）

## Deferred to Follow-Up Work

- `precision_fix_loop`（U4 真失败时无 LLM 自愈 —— 显式 deferred 到本计划外）
- LLM 自动产 test cases（design 阶段注入式交互）
- N=5 stress 跑本次闭环验证稳定性（ship gate 单独跟踪）

## Risks & Dependencies

- **R1：kernel 符号命名约定漂移**（reviewer feasibility KTD-1 / adversarial KTD-1）：op_api `l0op::OpAdd` 假设 kernel 入口叫 `OpAdd`，但 LLM 可能用 `op_add`/`OpAddKernel` 等其他命名。**Mitigation**：U2.5 fast-fail + codegen_kernel prompt 强约束（line U2 测试场景 + prompt 改动）。运行时 symbol mismatch 由 compile 阶段 `_check_stderr_fatal` 已涵盖。
- **R2：codegen_kernel prompt 改了 LLM 行为**（plan 改 prompt 风险）：U2.5 加 prompt 约束可能让 LLM 写出非自然 kernel（被强制命名）。**Mitigation**：U3 验证如果 prompt 改了导致 codegen 失败（其他命名约定断），可降级为仅"snippet-level"提示（"kernel 函数名建议 `OpPascal` 与 op_api 匹配"），不强制。
- **R3：validation.py line 104-105 已透传 report，U3 无需改**（reviewer feasibility U3 #5 / adversarial U3 #8）：如果实操发现 `success`/`error` 字段确实已透传，U3 仅做 e2e 报告增强；不需要碰 validation.py。
- **R4：HTD 树里 op_api/ 与 op_host/ 同级 vs d725530 描述"不注入 op_api/"**（reviewer scope-guardian HTD #6）：本计划 commit message 必须明确"方向 B 设计演进：从'不注入 op_api'演进到'scaffold 注入 op_api 与 LLM 写 kernel'，补回 ST 链路"。**这是设计演进，不是撤回**。
- **R5：测试文件 `tests/unit/test_validation.py` 当前不存在**（reviewer feasibility #6）：U3 新建 `tests/unit/test_validation.py`（之前 validation 模块无单测覆盖）—— 接受作为本 plan 的 scope 增量。

## Execution Progress (2026-07-30)

### U1 ✅ (ship)
- `cannbot_loader.py` 注入 op_api/ 4 文件参数化（`_OP_API_FILES` 4 元组）
- `_strip_opapi_section` 调用删除（保留函数体标 DEPRECATED）
- 21/21 单测过（12 文件断言 + op_api 参数化 + op_host 含 cust_opapi）
- Files changed: `src/ascend_op_agent/orchestrator/cannbot_loader.py`, `tests/unit/test_build_scaffold.py`, `tests/unit/test_codegen_subdir.py`

### U2 ✅ (ship)
- `validate_kernel_symbol` helper（snake_case CANN 9.1.0 filename stem 规则）
- `_kernel_symbol_validator` 节点插入 `codegen_proto` 之后 `review_fix` 之前
- codegen_kernel prompt 加强制 snake_case kernel 入口约定
- 10/10 单测过（happy snake_case / PascalCase 拒 / missing files / no l0op call）
- Files changed: `src/ascend_op_agent/orchestrator/nodes/common.py`, `src/ascend_op_agent/orchestrator/graphs/new_dev.py`, `tests/unit/test_kernel_symbol_validator.py`

### U3 ✅ (ship)
- `_main_single_report` 加 per-case metrics 展示（mere/mare for FP32, elems for INT32）
- `precision_report.success` + `error` 显示
- `make_test_cases_resolver` 标 DEPRECATED
- Files changed: `scripts/e2e_real_op.py`

### U4 ⚠️ partial (network blocked)
- **GLM-5.2 切换**：Minimax 配额耗尽（429）→ 切 Ark GLM-5.2（`auth_token: "${ARK_API_KEY}"` + `api_base: "https://ark.cn-beijing.volces.com/api/plan"` + `model: "glm-5.2"`）
- **e2e 跑通 14 阶段**：analyze → design → codegen_scaffold → codegen_kernel/host/proto → kernel_symbol_validator (passed) → review_fix → compile_fix_loop (clean) → precision (compile_not_ready first pass, race condition) → delivery → done
- **compile 实测**：910B 上 build.sh 产出 `custom_opp_almalinux_aarch64.run` + `OpAdd_*.o` ✓
- **install 实测**：`op_add_custom/op_api` 已 install 到 `/usr/local/Ascend/cann-9.1.0/opp/vendors/op_add_custom/op_api` ✓
- **ST build 实测**：`tests/st/build_st/test_aclnn_op_add` 二进制 build 成功 ✓
- **SSH 网络断**：100% packet loss（910B 网络 / VPN / 防火墙问题），ST driver run 未执行
- **预期**：SSH 恢复后跑 `./test_aclnn_op_add` 应 10/10 PASS（build 链路已验）

## Reviewer Notes (ce-doc-review 35 findings 摘要)

本计划已针对以下 5 个 fatal blockers 重写（35 findings 中前 5）：

| Finding | Reviewer | Resolution |
|---------|----------|------------|
| `_strip_opapi_section` 阻断 vendor `cust_opapi` | feasibility + adversarial | U1 删 line 668-670 |
| Vendor op_api/ 是 4 文件不是 3 文件，无 CMakeLists.txt | feasibility + adversarial | U1 改 `_OP_API_FILES` 为 4 文件；HTD 重画 |
| 不需要 `add_subdirectory(op_api)`（op_api library 在 op_host CMakeLists 内部） | feasibility + adversarial | U1 移除此 vendor 改动；HTD 修 |
| ST 自愈 sequencing bug（compile_fix_loop 在 precision 前 finished） | coherence + adversarial | KTD-4 明确"无 ST 自愈，error 透传" + deferred |
| vendor submodule 跨仓权限 | adversarial | 全部 in-repo 改，vendor 不动 |

其他 30 个 findings（行号修正、In-scope vs KTD-4 矛盾、测试文件引用、HTD 拓扑、错误类修正等）已 inline 修入对应章节。
