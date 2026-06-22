先把结论摆出来：做昇腾算子开发的 Agent，**别一上来就堆多 Agent，也别做成纯聊天机器人**。它本质是个有状态、长流程、强工具依赖、需要安全隔离的工程 Agent。最稳的架构是一条主线：

> **LangGraph 有状态工作流 + CLI/Script 工具层 + Skills 领域知识封装 + Docker/K8s 沙箱运行时 + 昇腾 CANN/NPU 环境 + 人工审批门禁。**

下面把每一层为什么这么选、具体怎么搭讲清楚。

## 一、先搞清楚这个 Agent 到底要解决什么

昇腾算子开发不是"写一段代码"，而是一整套工程链路：理解算法和数学定义、选择算子开发方式、生成算子工程、实现算子 kernel、编写算子原型/信息库/适配插件、编译部署、功能调试、精度比对、性能调优、上板验证，最后交付可复用算子包。

昇腾 CANN 本身就提供了一堆相关工具：

- **算子编译工具 op_compiler**：编译生成算子二进制文件
- **ATC 模型转换工具**：把开源框架模型或 Ascend IR 单算子描述文件转换成昇腾支持的 `.om` 离线模型
- **精度比对、AOE 自动调优、性能分析**等工具

MindStudio 更进一步，提供 msOpGen 算子工程生成、msOpST 算子测试、msDebug 调试、msSanitizer 异常检测、msOpProf 性能数据采集，以及 msKL 用于 Python 脚本中快速下发、编译和运行 Kernel。

所以你的 Agent 架构必须能**调用工具、读写工程文件、执行编译测试命令、分析日志、做多轮修复，并且整个过程可追踪、可回滚、可人工介入**。

---

## 二、为什么我不建议你一开始选这几类架构

把反面教材先排除掉，正面选型会更清楚。

### 不建议：早期 ReAct 式 Agent

单纯靠 LLM 自己决定"下一步调用什么工具"，在算子开发里风险太高。因为每一步都依赖昇腾环境、算子交付件结构、编译脚本、NPU 日志、精度数据和性能数据，**模型一旦理解错，就会浪费大量时间在错误路径上**。

### 不建议：纯 Chatbot

用户问一句、模型答一句，无法管理"算子设计 → 代码实现 → 编译 → 测试 → 调试 → 调优 → 验证 → 交付"这条长流程。**它缺的是状态管理和工具执行闭环**。

### 不建议：一上来就做复杂多 Agent

多 Agent 适合任务可并行、需要不同专家角色、需要对抗式评审的场景。但算子开发的主链路是强顺序的——你不先确定算子定义和规范，就没法正确实现；不先编译通过，就没法测试；不先功能正确，就没法谈性能调优。**过早引入多 Agent，只会增加状态同步成本和失败点**。

### 不建议：裸 CLI Agent

让模型直接在终端敲命令，确实灵活，但太危险。算子开发会涉及文件修改、编译、测试、NPU 运行，**必须有沙箱、权限控制、命令审批、日志追踪和回滚机制**。

---

## 三、总体架构：六层模型

推荐的整体架构从上到下分成六层，每一层各管一段：

```text
用户层：开发者 / 算法工程师 / CI Pipeline
        ↓
交互层：Web UI / VS Code 插件 / CLI / Chat
        ↓
Agent 编排层：LangGraph 有状态工作流
        ↓
Skills 层：AscendC Skill / Debug Skill / Tuning Skill / Delivery Skill
        ↓
工具层：CLI / Python Script / MCP Server
        ↓
运行环境层：Docker Sandbox / K8s Sandbox / 昇腾 NPU 环境
```

这套设计的**核心思想是：用刚性工作流保证交付确定性，用 Skills 沉淀昇腾领域知识，用 CLI/Script 执行真实工程操作，用沙箱保证安全**。

---

## 四、Agent 框架选型：LangGraph 做主干

### 首选 LangGraph

理由很直接：算子开发是有状态、多步骤、可中断、可恢复、可审计的长流程。LangGraph 把流程建模成 StateGraph，由 **State、Node、Edge、Checkpointer** 组成，天然适合表达"算子开发流程走到哪一步、当前状态是什么、下一步该执行哪个节点、失败后从哪里恢复"。

典型节点可以这样划分：

```text
用户输入 → 需求解析 → 算子规范生成 → 工程创建 → 代码实现 → 编译 → 单元测试 → 功能调试 → 精度比对 → 性能调优 → 上板验证 → 交付件打包 → 人工评审 → 完成
```

每个节点可以是一个 LLM 推理节点、一个工具执行节点、一个人机交互节点，或一个条件路由节点。

### 什么情况下才上多 Agent

如果你确实需要多角色协作，可以在 LangGraph 上层加 Supervisor 模式，或在某些阶段引入多 Agent，比如：

- **算子设计 Agent**：理解算法、输入输出、shape、dtype、约束
- **Ascend C 实现 Agent**：负责 kernel 实现
- **调试 Agent**：分析报错、日志、精度问题
- **性能调优 Agent**：分析瓶颈、生成调优建议
- **评审 Agent**：检查代码规范、算子交付件完整性

但记住一条原则：**多 Agent 是后期增强项，不是起点**。

### Cursor / Claude Code 类工具怎么定位

Cursor、Claude Code 这类 Agent 编程工具很适合"代码编辑、跨文件修改、测试修复、局部重构"，但它们更偏开发者 IDE 辅助，不一定擅长完整算子交付流程的编排、状态恢复、审计、权限控制和系统集成。

所以我的建议是**分工**：

- **LangGraph Agent**：负责完整算子开发流程编排
- **Cursor / Claude Code**：作为开发者的日常编码助手
- **两者通过 Git、Patch、Diff、日志、CI 结果联动**

---

## 五、Prompt 架构：渐进式加载，别塞超大 System Prompt

这里正好承接你前面总结文章里的核心观点——Prompt 要从"高耦合小作文"演进成"系统稳定 + 渐进式加载"。

### System Prompt 只放不变的东西

```text
你是昇腾算子开发助手。
你的目标是帮助用户完成从算子需求到可交付 Ascend C / TBE DSL / AI CPU 算子包的开发、调试、调优和验证。
你必须优先使用工具获取真实环境信息、文档、代码、日志和测试结果。
你不能凭空编造昇腾 API、算子接口、编译命令或硬件行为。
所有修改必须通过工具执行，并在日志中记录。
遇到编译错误、运行时错误、精度误差、性能瓶颈时，应先分析原因，再提出修复方案。
```

### 动态加载的 Skills 和知识文件

把这些做成按需加载的 Markdown / YAML / Script 组合：

```text
SKILL-ASCEND-C-BASICS.md
SKILL-OPERATOR-SPEC.md
SKILL-OP-PROJECT-STRUCTURE.md
SKILL-TILING.md
SKILL-DEBUGGING.md
SKILL-PERF-TUNING.md
SKILL-DELIVERY-CHECKLIST.md
SKILL-NPU-VALIDATION.md
SKILL-PRECISION-COMPARE.md
```

运行时根据当前阶段加载对应 Skill，避免 System Prompt 膨胀。

### 用户/项目级配置也拆出去

```text
USER.md        # 用户偏好、常用硬件、调试习惯
PROJECT.md     # 当前算子项目背景、依赖、环境路径
ENV.md        # CANN 版本、NPU 设备、Docker 镜像、测试数据集
```

---

## 六、Planning 架构：长程规划 + 刚性主流程

### 全局规划器：生成算子开发计划

用户丢过来一个需求，比如"帮我实现一个 FlashAttention 类型的矢量算子，支持 float16，输入 shape 为 [B, S, D]"，Agent 先生成结构化计划：

```json
{
  "operator_name": "flash_attention_v2",
  "inputs": [...],
  "outputs": [...],
  "compute_definition": "...",
  "development_mode": "Ascend C",
  "delivery_artifacts": [
    "op_proto",
    "kernel_impl",
    "host_tiling",
    "op_info_cfg",
    "test_cases",
    "build_scripts"
  ],
  "verify_steps": [
    "cpu_functional_test",
    "npu_functional_test",
    "precision_compare",
    "performance_profile"
  ]
}
```

### 阶段规划器：每个阶段拆子任务

拿"代码实现"阶段举例，它可以拆成：

```text
1. 实现算子原型定义
2. 实现 Kernel 核函数
3. 实现 Tiling 计算
4. 实现 Host 侧调用逻辑
5. 编写单元测试
6. 编写构建脚本
```

### 本地执行器：负责真正动手

这部分不靠模型"想象自己执行了"，而是调用 CLI、Script、编译器、测试框架。

### 反思修复器：看结果再决定下一步

执行完后，Agent 拿到结果分类处理：

```text
编译成功 → 进入测试
编译失败 → 分析日志 → 修复代码 → 重新编译
测试失败 → 分析 diff → 定位 bug → 修复
精度异常 → 调用精度比对工具 → 分析原因
性能不达标 → 调用性能采集工具 → 给出调优建议
```

---

## 七、Memory 架构：短期状态 + 长期知识双轨

### 短期记忆：当前任务状态

用一个强结构化的 OperatorDevState 贯穿全程：

```json
{
  "task_id": "op-dev-20260621-001",
  "operator_name": "custom_matmul",
  "status": "debugging",
  "current_stage": "npu_validation",
  "workspace": "/workspace/operators/custom_matmul",
  "cann_version": "8.0.RC3",
  "last_error": "kernel launch failed: ...",
  "retry_count": 2,
  "human_approval_required": true
}
```

LangGraph 的 Checkpointer 正好能把这种状态持久化下来，支持中断、恢复和审计。

### 长期记忆：三类分开存

- **事项型记忆**：记录"某类算子常见的 tiling 错误""某 CANN 版本下 msDebug 的使用方式""某硬件上常见的内存越界问题"，用 `MEMORY.md` 或数据库存
- **知识型记忆**：维护昇腾开发知识库，包括 Ascend C 编程模型、SPMD、核函数、存储单元、矢量/矩阵 API、Tiling、double buffer、workspace 等，用文件系统 + 向量检索混合管理
- **经验型记忆**：每次算子开发完自动沉淀经验，比如"这个算子最初因为 Tiling 拆分不合理导致 UB 越界，后来通过 xxx 方式修复"

---

## 八、Tools 架构：CLI / Script 为主，MCP 为辅

### 第一优先级：CLI 工具

因为昇腾开发本身就有大量命令行操作——CANN 算子编译工具、ATC、AOE、精度比对、性能分析、MindStudio 相关工具，都可以被 Agent 直接调用。

```bash
# 示例命令
op_compiler ...
atc ...
aoe ...
msOpST ...
msDebug ...
msSanitizer ...
msOpProf ...
```

### 第二优先级：Python / Shell Script

Script 的价值是把复杂流程封装起来，比如：

```bash
create_op_project.py
build_op.py
run_op_test.py
compare_precision.py
profile_op.py
package_delivery.py
```

脚本内部处理参数校验、环境检测、日志收集、错误分类和结果返回，Agent 只管"调用哪个脚本 + 传什么参数"。

### 第三优先级：MCP Server

MCP 适合连接外部系统，而不适合承载所有底层逻辑。你可以搭这些 MCP Server：

```text
Ascend Docs MCP：查询昇腾官方文档
Operator Repo MCP：读取已有算子仓库
CANN Environment MCP：查询 CANN 版本、NPU 状态、环境变量
Test Platform MCP：提交测试任务、拉取结果
CI/CD MCP：触发流水线、获取构建结果
```

**最终工具层长这样**：

```text
用户意图
  ↓
LangGraph Router
  ↓
Skill 选择
  ↓
Script / CLI 执行
  ↓
MCP 查询外部系统
  ↓
结果返回 Agent
```

---

## 九、Workflow 架构：刚性主流程 + 可复用 Skill

### 主流程必须刚性

因为算子开发是工程交付，不是开放式聊天。主流程建议固定成这条线：

```text
需求澄清 → 算子规范 → 开发方式选择 → 工程创建 → 原型定义 → Kernel 实现 → Tiling 实现 → 编译 → 单元测试 → 功能调试 → 精度比对 → 性能调优 → 上板验证 → 交付件检查 → 人工验收
```

### 每个阶段封装成 Skill

```text
RequirementSkill    → 把用户需求转成结构化算子规范
SpecSkill          → 生成算子输入输出、shape、dtype、约束
ProjectSkill       → 创建算子工程目录
PrototypeSkill     → 生成算子原型定义
KernelSkill        → 生成 Ascend C / TBE DSL 实现
TilingSkill        → 生成 Tiling 策略和 Host 侧代码
BuildSkill         → 编译算子包
TestSkill          → 运行单算子测试
DebugSkill         → 调用 msDebug / msSanitizer
PrecisionSkill     → 调用精度比对工具
ProfileSkill       → 调用 msOpProf / MindStudio Insight
DeliverySkill      → 检查交付件完整性并打包
```

这样既保留了 Workflow 的确定性，又拥有了 Skills 的灵活性。

---

## 十、Environment 架构：沙箱 + 昇腾环境是刚需

这一层绝不能省。昇腾 Ascend C 开发前要安装驱动固件和 CANN 软件包，开发过程涉及 NPU 侧调用、CPU 域调试、NPU 域上板调试、算子入图、PyTorch/ONNX/TensorFlow 框架适配等环节。

### 本地开发环境

```text
Workspace：/workspace/agent-runs/<task-id>/
Operator projects
Log directory
Artifact storage
Git repo
Temporary files
```

### Docker 沙箱

```dockerfile
FROM ascend-cann:8.x

RUN apt install -y git python3 cmake
RUN pip install pytest

WORKDIR /workspace
USER agent-executor
```

沙箱里至少限制这些：

```text
CPU / memory limits
Disk quotas
Network egress control
Read-only system directories
Allowed command whitelist
```

### NPU 运行环境

测试和上板验证需要真实的昇腾运行环境，建议单独做成：

```text
NPU Pool：多台昇腾设备
Job Queue：任务排队
Artifact Store：存放算子包、日志、性能数据
Result Service：返回验证结果给 Agent
```

---

## 十一、Human-in-the-loop：关键节点必须人工审批

算子开发不是全自动生成代码，而是工程交付。建议在这些节点加人工审批：

```text
算子规范确认
首次代码生成确认
编译通过后进入 NPU 测试前
精度异常修复方案确认
性能调优参数变更确认
最终交付件发布确认
```

LangGraph 的 `interrupt()` 机制正好适合这类"执行到关键节点暂停、等人工确认后再继续"的场景。

审批信息尽量结构化，别只甩一句"是否继续"：

```json
{
  "stage": "enter_npu_validation",
  "risk": "medium",
  "changes": [
    "modified kernel/default.cpp",
    "modified tiling/default_tiling.h"
  ],
  "command": "python run_op_test.py --device npu",
  "estimated_duration": "5min",
  "requires_npu": true
}
```

---

## 十二、Skills 设计：昇腾领域知识的核心载体

### OperatorSpec Skill

负责把用户需求转成标准算子规范：

```markdown
## Operator Specification Skill

当你收到用户算子需求时，必须提取以下内容：

- operator_name
- inputs / outputs
- data types
- shape constraints
- layout format
- compute definition
- performance requirements
- precision tolerance
- target development mode

如果信息不完整，必须先向用户提问，不能猜测。
```

### AscendC Skill

封装 Ascend C 开发知识：

```markdown
## Ascend C Development Skill

你需要遵循以下开发流程：

1. 理解算子计算逻辑
2. 定义输入输出 tensor
3. 设计 Tiling 策略
4. 实现核函数
5. 实现 Host 侧 Tiling
6. 编写测试
7. 编译验证
8. 上板调试
9. 性能调优

涉及 SPMD 编程模型、多核并行、流水并行、同步控制、数据搬运、矢量/矩阵计算时，必须优先查阅官方 API 文档。
```

### Debug Skill

```markdown
## Debug Skill

遇到错误时，按以下顺序分析：

1. 编译错误：检查 API 使用、头文件、CMakeLists、算子原型
2. 链接错误：检查算子信息库、插件、动态库路径
3. 运行时错误：检查 tensor shape、dtype、layout、workspace
4. 精度错误：调用精度比对工具，定位误差来源
5. 性能问题：采集 profiling 数据，分析瓶颈

不要直接修改代码，必须先给出诊断结论。
```

### Delivery Skill

```markdown
## Delivery Skill

算子交付前必须检查：

- 算子原型定义是否完整
- Kernel 实现是否存在
- Host Tiling 是否正确
- 算子信息库是否配置
- 框架适配插件是否完整
- 单元测试是否通过
- 精度比对是否通过
- 性能是否满足目标
- README 和构建脚本是否完整
```

---

## 十三、落地路线：分四个阶段推进

### 第一阶段：最小可用版本 MVP

先实现这条链路：

```text
用户输入算子需求
↓
生成算子规范
↓
创建算子工程
↓
生成 Ascend C skeleton
↓
调用编译脚本
↓
返回编译结果
```

工具先用这几个：

```bash
read_file
write_file
apply_patch
run_shell_command
git_diff
build_op
```

这个阶段别急着做多 Agent、别急着接 NPU、别急着做复杂调优。

### 第二阶段：加入调试和验证

增加这些能力：

```text
编译错误修复
单元测试生成
日志分析
msDebug / msSanitizer 调用
精度比对
失败用例定位
```

这时 Agent 从"代码生成器"升级成"开发助手"。

### 第三阶段：接入 NPU 和调优

增加：

```text
NPU 上板验证
性能数据采集
瓶颈分析
Tiling 优化建议
Double buffer / workspace / pipeline 优化
多版本性能对比
```

这时 Agent 才真正触及昇腾算子开发的核心价值。

### 第四阶段：沉淀自进化能力

完成一定数量的算子开发后，让 Agent 自动沉淀：

```text
常见错误模式库
Tiling 模板库
调试经验库
性能优化案例库
算子交付检查清单
项目级偏好配置
```

到这一步，Agent 就从"一次性执行工具"变成"会持续变强的算子工程资产"。

---

## 十四、最终推荐架构一览

```text
                    ┌────────────────────┐
                    │    用户交互层        │
                    │ Web / CLI / IDE    │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │  LangGraph 编排层   │
                    │ State / Node / Edge │
                    │ Checkpointer        │
                    │ Human Approval      │
                    └─────────┬──────────┘
                              │
      ┌───────────┬───────────┼────────────┬────────────┐
      │           │           │            │            │
┌─────▼─────┐┌───▼───┐┌─────▼─────┐┌─────▼─────┐┌───▼──────┐
│ Requirement││ Spec  ││ AscendC   ││ Debug     ││ Tuning   │
│ Skill     ││ Skill ││ Skill     ││ Skill     ││ Skill    │
└───────────┘└───────┘└───────────┘└───────────┘└──────────┘
      │           │           │            │            │
      └───────────┴───────────┴────────────┴────────────┘
                                │
                    ┌───────────▼──────────┐
                    │      工具执行层         │
                    │ CLI / Script / MCP     │
                    │ CANN / MindStudio      │
                    │ NPU / Simulator        │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼──────────┐
                    │      沙箱运行层         │
                    │ Docker / K8s          │
                    │ 文件系统 / 日志 / 状态  │
                    │ 资源限制 / 安全隔离     │
                    └────────────────────────┘
```

对应到具体选型，就是这样一套组合：

| 层级 | 推荐方案 |
|---|---|
| Agent 编排 | LangGraph |
| 多 Agent | 后期引入，前期单 Agent |
| 编码助手 | Cursor / Claude Code 辅助 |
| Prompt | 渐进式 Skill 加载 |
| Planning | 长程规划 + 刚性主流程 |
| Memory | 短期状态 + 长期知识库 |
| Tools | CLI / Script 为主，MCP 为辅 |
| Workflow | LangGraph 固定主流程 + Skill 动态执行 |
| Environment | Docker 沙箱 + 昇腾 NPU 环境 |
| 安全 | 命令白名单、权限最小化、人工审批、日志审计 |
| 自进化 | 沉淀错误模式、Tiling 模板、调试经验、交付清单 |

如果你愿意，下一步我可以先帮你把**第一阶段 MVP** 拆出来——把 Prompt、Planning、Memory、Tools、Workflow、Environment 这六个模块各自的具体实现方式和优先级列清楚，让你能直接动手搭。