# 交付件规范修复（问题 1+2+3）

## 背景

e2e 跑出的交付件有 3 个规范问题（详见 2026-07-29 交付件审查）：

1. **精度验证对象错**：ST 驱动测 `add_example` 非 `op_add`（precision JSON `operator_name=add_example`，10/10 PASS 验证的是 scaffold 原版算子，不是 LLM 生成的 op_add）
2. **scaffold 残留**：`.run` package 含 `add_example` + `op_add` 两套 kernel（远程 `/home/hsl/e2e_ops/op_add/` 历史残留未清，`_rsync_to_npu` 只覆盖同名不删旧文件）
3. **build.sh 包名 bug**：[build.sh:212](file:///tmp/e2e_ops_local/op_add/build.sh) 硬编码 `custom_opp_ubuntu_aarch64.run`，910B 容器是 almalinux，文件名不匹配 -> `[ERROR] Package not found` -> `return_code=1`（之前误判 cosmetic，实际是 build.sh bug）

## 修复方案（用户选「两者结合」：参数化 ST 模板 + LLM 改 ComputeGolden）

### 修复 1：build.sh 包名 bug（问题 3）— 小改

- **文件**：`src/ascend_op_agent/orchestrator/cannbot_loader.py`
- **函数**：`load_build_scaffold`
- **改动**：对 build.sh 内容后处理，把包名检测改为通配符
  - before: `PKG_PATH="${BUILD_PATH}/custom_opp_ubuntu_aarch64.run"`
  - after: `PKG_PATH=$(find ${BUILD_PATH} -maxdepth 1 -name "custom_opp_*.run" | head -1)`
- **效果**：build.sh package check 不再硬编码 distro，`return_code` 字段准确反映编译结果

### 修复 2：远程残留（问题 2）— 小改

- **文件**：`scripts/e2e_real_op.py`
- **函数**：`_rsync_to_npu`
- **改动**：rsync 前清远程目录（`rm -rf {remote_dir}/*` 再 tar 解包），避免历史 add_example 残留
- **效果**：`.run` 只含 op_add 一套 kernel

### 修复 3：精度验证对象错（问题 1，两者结合）— 中改

- **3a. `cannbot_loader.load_build_scaffold` 扩展**：注入 `tests/st/` 模板
  - 新增注入文件：`tests/st/test_aclnn_{op}.cpp` + `tests/st/CMakeLists.txt` + `tests/st/run.sh`
  - 参数化替换：`add_example` -> `{op_snake}` / `AddExample` -> `{op_pascal}` / `aclnnAddExample` -> `aclnn{op_pascal}` / `aclnn_add_example.h` -> `aclnn_{op_snake}.h`
  - 模板源：vendor `add_example/tests/st/` 原版（含 ComputeGolden add 逻辑 + MERE/MARE 比对）
- **3b. `new_dev.py` `_scaffold_inject_node` 扩展**：把 `load_build_scaffold` 返回的 `tests/st/*` 文件写入工程目录
- **3c. `new_dev.py` 新增 `codegen_st` 节点**：LLM 基于 op_info + design_doc 改 `tests/st/test_aclnn_{op}.cpp` 的 `ComputeGolden` 函数体（算子语义部分）
  - 对 add 算子：参数化模板的 golden（`output = x1 + x2`）已正确，LLM 改是 no-op
  - 对其他算子：LLM 改 golden 逻辑（如 multiply -> `output = x1 * x2`）
  - 只改 ComputeGolden 函数体，其他部分（include/API/比对）保持参数化模板
- **3d. `e2e_real_op.py` `op_name_resolver`**：用 `op_info.name`（不 fallback `add_example`）
- **效果**：precision 验证 op_add（`operator_name=op_add`），ST 驱动测 LLM 生成的 op_add kernel

## 测试

- **单测**：
  - `tests/unit/test_build_scaffold.py`：验证 ST 模板参数化（add_example->op_add 全替换）+ build.sh 通配符
  - `tests/unit/orchestrator/test_new_dev_graph.py`：codegen_st 节点接入图
- **e2e**：单次 `PYTHONPATH=src python scripts/e2e_real_op.py`
  - 验证 precision `operator_name=op_add`
  - 验证 `.run` 只含 op_add（无 add_example 残留）
  - 验证 `return_code=0`（build.sh 包名修复）

## 限制

- `codegen_st` LLM 改 ComputeGolden 对非 add 算子可能不稳定（本次不接 fix_loop，先验证 add 算子闭环）
- 非 add 算子的 ST 驱动稳定性（LLM 生成 C++ golden）留后续迭代
- 本次只改 e2e 路径（`use_scaffold_codegen=True`）；生产路径（`use_scaffold_codegen=False`）的 ST 驱动生成待 Path A/B 真实场景验证

## 不改的部分

- `npu_exec.run_st_driver` 逻辑不变（已支持 op_name 参数，line 700 `test_aclnn_{op_name}` + line 688 `vendors/{op_name}_custom`）
- `validation.make_real_precision_node` 不变（已支持 operator_name_resolver）
- vendor `add_example` 原版不动（submodule，参数化在 load_build_scaffold 里做）
