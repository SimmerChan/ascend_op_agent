# 2026-07-29-003 LLM 命名飘移防御

## Context

2026-07-29 d725530 修复了交付件规范（scaffold 注入 + 包名 + 远程清理），但 e2e 真编译仍 fail：

```
[ERROR] Op[VectorAdd] of index[0] compile failed, kernelName: VectorAdd_0f65bcb3...
gmake[3]: *** [op_kernel/CMakeFiles/VectorAdd_ascend910b.dir/build.make:70: ...] Error 1
```

根因分析：
- 之前担心：LLM codegen hallucinate `add_custom_*` 命名，scaffold CMakeLists 引 `op_add_*` → No rule to make target
- 实际 e2e：LLM 没用 add_custom_ 错前缀，**用了正确** `vector_add_*` 命名，CMakeLists 引用对得上
- 真正的 fail：LLM 写出的 `vector_add_arch22.cpp` 里 `compile_op(src, origin_func_name=vector_add, ...)` 调用的 kernel_meta 在编译链生成失败（找不到 `.o`）
- 这是 LLM codegen 代码质量问题（kernel 内部 `compile_op` 调用 + tiling_key + tiling_data 不完整），非本次命名防御范围

但 d725530 之前暴露的 `add_custom_def.cpp` 残留是真问题（2026-07-29 远程 `/home/hsl/e2e_ops/op_add/op_host/` 实测发现），LLM 命名飘移**确实**会发生。本次修复对此做主动防御。

## Fix 设计

### 1A. analyze 阶段产出结构化 `<<OP_INFO>>` 块

- **位置**：`src/ascend_op_agent/orchestrator/graphs/new_dev.py` 的 `analyze_node.task_prompt_template`
- **机制**：prompt 末尾强制 LLM 产 `<<OP_INFO>>{"name": "...", "class_name": "..."}<<END>>` JSON 块
- **解析**：`src/ascend_op_agent/orchestrator/nodes/common.py` 新增 `parse_op_info_block(response)`
- **写入**：在 `make_llm_node._node` 里统一调 parser，匹配则写 `update["op_info"] = {name, class_name}`
- **下游**：codegen 节点已通过 `{state}` 模板把 state 传给 LLM，LLM 可读 `state.op_info.name` 命名文件

### 1B. codegen 后处理文件名校验

- **位置**：`src/ascend_op_agent/orchestrator/nodes/common.py` 新增 `enforce_op_naming(files, op_snake)`
- **机制**：扫 `code_result.files`，识别 `op_host/ op_kernel/ op_graph/` 下的 cpp/h 文件，若 basename 以已知错前缀开头（`add_example` / `add_custom` / `AddExample` / `AddCustom`），rename 到 `{op_snake}_*` 并同步替换 content
- **注入点**：`make_llm_node._node` 在 markdown fallback 提取文件后、合并到 `code_result.files` 前调用
- **观测**：rename 次数写入 `code_result.naming_renamed` + `last_phase_result.naming_renamed`

## 验证

### 单测

`tests/unit/test_op_naming_guard.py` —— 15 个测试全过：

| 测试组 | 覆盖 |
|--------|------|
| `parse_op_info_block_*` (6) | 基本解析、class_name fallback、缺块/JSON 错/类型错均返 None 不抛 |
| `enforce_op_naming_*` (5) | add_custom/add_example rename、正确命名不动、非语义目录不动、空 op_snake 跳过 |
| `make_llm_node_*` (4) | response OP_INFO 块 → state.op_info、markdown 错前缀 → 自动 rename、缺 op_info 降级不动 |

### 回归

- 全量单测：773 passed + 4 skipped（无回归）
- ship gate `lint` + `unit_test` 两步过

### 远程清理

- e2e 暴露 `/home/hsl/e2e_ops/op_add/op_host/` 残留 `add_custom_def.cpp / add_custom_infershape.cpp`（d725530 之前的失败 e2e 产物）
- 本次手动清：`ssh ... rm -rf /home/hsl/e2e_ops/op_add`，后续 `_rsync_to_npu` 会在每次 e2e 前自动清

### E2E（已知限制）

- 当前 e2e 跑 vector_add：compile 仍 fail（kernel_meta `.o` 找不到）
- 根因：LLM 写的 vector_add kernel 内部 `compile_op` 调用不完整（kernel function name + tiling_key 链缺失），非命名防御范围
- 本次防御对 `add_custom_*` 命名飘移已生效（实测无残留），但需要后续 plan 单独修 LLM codegen 质量（增加更完整 few-shot + 后处理校验 kernel 完整性）

## 改动文件

| 文件 | 改动 |
|------|------|
| `src/ascend_op_agent/orchestrator/nodes/common.py` | +2 helper (`parse_op_info_block`, `enforce_op_naming`)、`_node` 注入 2 个 hook、模块 docstring 加 U5 说明 |
| `src/ascend_op_agent/orchestrator/graphs/new_dev.py` | analyze prompt 加 `<<OP_INFO>>` 强制块 |
| `tests/unit/test_op_naming_guard.py` | 新增 15 个测试 |
