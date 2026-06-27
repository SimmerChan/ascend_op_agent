---
title: "U0.5: 算子 NPU 运行路径 spike 报告"
type: e2e
status: success
date: 2026-06-27
parent_plan: docs/plans/2026-06-26-002-feat-post-p0-completeness-robustness-plan.md
---

# U0.5: 算子 NPU 运行路径 spike 报告

## 结论(TL;DR)

Plan 的核心假设(`msoput` 存在 + `test_main` 自动生成)**全错**。真实的 CANN 9.1.0 跑算子路径是 **手写 C++ aclnn ST 驱动**(scaffold 自带 `tests/st/test_aclnn_add_example.cpp`),驱动内部做 NPU 运行 + CPU golden + MERE/MARE 精度比对。**U1 + U4 大幅简化合并**为"构建+跑 ST 驱动,解析 stdout"。

实测端到端跑通: **10/10 测试 PASS, exit 0**。

---

## Plan 假设 vs 真实(逐条对照)

| Plan 假设(原 U0.5/U1) | 真实情况 | 影响 |
|------------------------|----------|------|
| `msoput` 二进制存在 | ❌ **不存在**。CANN 9.1.0 只有 `msopgen` + `msopst` | U1 命令重写 |
| `build.sh` 生成 `<path>/build_out/test_main` | ❌ **不生成**。build.sh 只产 `custom_opp_*.run` 包 | U1 入口换 |
| `test_main --op=X --input=y --output=z` | ❌ 不存在此 CLI | U1 解析换 |
| NPU 跑算子拿 actual 数组,再 Python 算 cos_sim | ⚠️ 部分。**驱动内部自己算好了 MERE/MARE** | U1+U4 合并 |

---

## 真实路径(已端到端验证)

### 完整 6 步(全部实测通过)

```bash
# 0. 前置: source CANN env(每次 docker exec 新 session 必做)
source /usr/local/Ascend/ascend-toolkit/set_env.sh

# 1. 编译算子工程(已在 U13 compile node 实现,产 custom_opp_*.run)
cd <operator_path> && bash build.sh --soc=ascend910b
# → <operator_path>/build/custom_opp_almalinux_aarch64.run

# 2. 安装 op 包(需 --force,见下方"坑")
cd <operator_path>/build && ./custom_opp_almalinux_aarch64.run --force
# → /usr/local/Ascend/cann-9.1.0/opp/vendors/add_example_custom/

# 3. 设 LD_LIBRARY_PATH(驱动 .so 依赖)
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/opp/vendors/add_example_custom/op_api/lib/:$LD_LIBRARY_PATH

# 4. 构建 ST 驱动(cmake + make)
cd <operator_path>/tests/st && mkdir -p build_st && cd build_st && cmake .. && make -j4
# → tests/st/build_st/test_aclnn_add_example

# 5. 跑 ST 驱动(NPU 真跑 + CPU golden + MERE/MARE 比对,全内置)
./test_aclnn_add_example

# 6. 解析 stdout 拿 pass/fail + 指标(见下方格式)
```

### 实测 stdout 格式(关键 —— U1 解析依据)

```
========================================
add_example 算子 ST 测试
========================================
模式: Real (NPU)
...
测试: FP32 基础加法
[Real] 测试 - size=6
  [PASS] MERE=0.00e+00, MARE=0.00e+00 (threshold=1.22e-04, 6 elems)
...
========================================
测试报告
========================================
总计: 10
通过: 10
失败: 0
========================================
```

**Exit code**: `0` = 全 pass,非 0 = 有 fail(实测全 pass 时 exit=0)。

**解析锚点**(U1 用 regex 提):
- 每条 case: `^\s+\[(PASS|FAIL)\] MERE=([\d.eE+-]+), MARE=([\d.eE+-]+) \(threshold=([\d.eE+-]+), (\d+) elems\)`
- INT32 case(无 MERE/MARE): `^\s+\[(PASS|FAIL)\] 所有 (\d+) 个元素一致`
- 总结: `总计: (\d+)` / `通过: (\d+)` / `失败: (\d+)`

---

## U1 + U4 重新定义(基于 spike)

原 plan: U1 = `run_operator` 拿 actuals,U4 = `make_real_precision_node` 算 metrics。**两 unit 合并**:

**新 U1 (`run_st_driver`)**:
- 输入: `operator_path`(已编译过的工程)
- 步骤: install .run(--force) → set LD_LIBRARY_PATH → cmake+make tests/st → 跑驱动 → 解析 stdout
- 输出: `PrecisionReport` dict(直接驱动算好的): `{total, passed, failed, cases: [{name, passed, mere, mare, threshold, elems}]}`

**新 U4**: `make_real_precision_node` 内部调 `executor.run_st_driver` 拿现成 PrecisionReport(不再单独算 cos_sim;驱动的 MERE/MARE 是 CANN 社区标准,比 plan 的 cos_sim 更权威)。test_cases_resolver 改为驱动内置(不再从 state 注入 golden/actual)。

**精简**: 原 plan 假设的 `run_operator` 返回 `list[np.ndarray]` + Python 算 `compute_precision_metrics` 这条线**废弃**,改用驱动内置比对。`compute_precision_metrics` 保留作 fallback(numpy-only 退化路径,U0.5 验证失败时用 —— 但本次验证成功,主路径走驱动)。

---

## 坑(踩到的 3 个,需 U1 处理)

### 坑 1: ops_pt 容器 cgroup 损坏
- 症状: `docker exec ops_pt ...` 报 `cgroup.procs: no such file or directory`
- 原因: docker/systemd cgroup 被清理但容器进程残留
- 修复: `docker restart ops_pt`(实测有效,卷数据不丢)
- **U1/U7 必须**: SSH 包装层加 cgroup 错误检测,报"请重启容器"而非裸 traceback

### 坑 2: op 包安装需 --force
- 症状: `./custom_opp_*.run` 默认安装失败,报 `libcust_opapi.so: undefined symbol: _ZN4l0op10Contiguous...`
- 原因: 安装时 validator 找不到 `l0op::Contiguous` 符号(CANN 运行时提供,但 install-time validator 链接不全)
- 修复: `--force` 跳过 validation。运行时 OPP 正常解析该符号(实测 ST 驱动能跑)
- **U1 必须**: install 命令固定加 `--force`

### 坑 3: LD_LIBRARY_PATH
- 症状: ST 驱动运行时报找不到 lib
- 修复: `export LD_LIBRARY_PATH=<vendors>/<name>/op_api/lib/:$LD_LIBRARY_PATH`
- **U1 必须**: 跑驱动前设此 env(每次 docker exec 新 session 都要)

---

## 产物

- ST 驱动二进制: `/tmp/op_test/tests/st/build_st/test_aclnn_add_example`(已构建)
- 已安装 op 包: `/usr/local/Ascend/cann-9.1.0/opp/vendors/add_example_custom/`
- stdout 全量样本: `/tmp/st_out.txt`(910B 容器内)

## Verification

`./test_aclnn_add_example` exit 0,10/10 测试 PASS,总计/通过/失败 行可解析。U1 spec 锁定:ST 驱动路径,不再依赖 msoput/test_main 假设。

---

## 对 Plan 的修正(回填到 U1/U4)

- U1 重命名: `run_operator` → `run_st_driver`(语义准确)
- U1 输出: `list[np.ndarray]` → `PrecisionReport dict`(驱动算好的)
- U4 简化: 不再调 `compute_precision_metrics`,直接用驱动报告
- U0.5 Dependencies 移除"test_main"字样,改为"ST 驱动"
- 风险表删 "msOpUT 路径未验证"(已验证),加 "cgroup/LD_LIBRARY_PATH/--force 运维坑"
