# Ascend Op Agent

> 昇腾算子开发 Agent —— AI Agent 驱动的运行时引擎。用刚性状态机保证交付确定性，用 Skill 知识层沉淀昇腾领域经验，用 910B 真编译 + ST 驱动验证确保算子质量。

**状态**：v1.0.0 已发布（2026-07-06）｜ ship gate 4/4 PASS ｜ N=20 stress 100% PASS ｜ 426 tests passed

---

## 项目目的

GPU 工程师把算子迁移到 AscendC 时，每次都得从头摸索一整套工程链路——理解算法定义、生成工程骨架、实现 kernel、编写宿主机代码、编译部署、精度比对、性能调优。**样板代码耗时数小时，开发上下文无法跨会话存活，积累的经验难以复用。**

Ascend Op Agent 把这套流程抽象为**刚性状态机工作流**，由 Agent 自主驱动：

- **消除样板代码**：PhaseRunner 自动编排「分析 → 设计 → 代码生成 → 编译 → 精度验证」全流程，Agent 只写语义代码
- **跨会话上下文存活**：CheckpointStore 每节点持久化，进程崩溃可从断点续跑；经验固化为 Skill 复用
- **真编译真验证**：直接对接 910B 硬件 + CANN 工具链，非模拟器，暴露真实编译问题与精度偏差

## 核心功能

| 能力 | 实现 | 价值 |
|------|------|------|
| **状态机编排** | 自研 PhaseRunner（13 节点顺序 DAG + 条件路由 + HITL） | 保证交付流程确定性，不走随机路径 |
| **崩溃恢复** | CheckpointStore（SQLite v2 + WAL + v1↔v2 迁移） | 每节点后持久化，进程崩溃从断点续跑 |
| **闭环修复** | fix_loop（review → fix → re-review 收敛） | 编译/精度问题自动迭代修复，减少人工介入 |
| **910B 真编译** | NpuExecutor（SSH → docker exec ops_pt → build.sh） | 真实昇腾硬件编译，非模拟器 |
| **真算子验证** | ST 驱动（NPU 执行 + CPU golden + MERE/MARE 比对） | CANN 社区标准精度验证 |
| **知识层** | cannbot-skills 加载器 + Skill 结晶化（`/learn`） | 消费华为官方 Skill，经验自动固化复用 |
| **多 Provider** | Minimax / 智谱 GLM / 火山 Ark 三 provider 轮询 | 配额互备，streaming + per-provider max_tokens 自适应 |
| **多入口** | CLI + 双进程 TUI + ACP（VS Code/Zed/JetBrains） | 融入开发者现有工作流 |

## 三条用户路径

共享同一套运行时后端（PhaseRunner + checkpoint + NpuExecutor），仅前端入口不同：

| 路径 | 场景 | 输入 | 状态 |
|:----:|------|------|:----:|
| **B** | 单算子迁移 | CUDA / Triton 源代码 | ✅ 已交付（P0） |
| **C** | 单算子全新开发 | 算子需求描述 | ✅ 已交付（P0） |
| **A** | 模型级选择性批量迁移 | 客户模型（含 N 个算子的 python 脚本/repo） | 🟡 规划中（P2；U1 spike 结论 NO-GO，待重开分期决策） |

## 架构概览

```
用户入口（CLI / TUI / ACP-IDE）
        │
        ▼
┌──────────────────────────────────────────────────┐
│  编排层  PhaseRunner 状态机（orchestrator/）      │
│  entry → analyze → design(HITL) → codegen        │
│   → review_fix → compile → precision(ST 驱动)    │
│   → delivery_mode → framework_adapt → done       │
│  + CheckpointStore + fix_loop + NpuExecutor      │
└──────────────────────┬───────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌──────────────────┐      ┌──────────────────────┐
│ Agent 核心引擎   │      │ 910B 远程环境        │
│ AIAgent          │      │ ssh 192.168.9.105    │
│ + 7 层 Prompt    │      │ → docker exec ops_pt │
│ + 四层记忆       │      │ → CANN 9.1.0 aarch64 │
└──────────────────┘      └──────────────────────┘
          │
          ▼
┌────────────────────────────────────┐
│ 知识层  cannbot-skills 加载        │
│ + Skill 结晶化（/learn）           │
└────────────────────────────────────┘
```

## 安装部署

### 环境要求

- **Python** >= 3.10
- **Node.js** >= 16（仅 TUI 模式需要）
- **910B 远程环境**（算子编译/验证硬件；本地无 NPU 也可跑 Agent 逻辑，只是 compile/precision 节点降级）

### 安装

```bash
# 克隆（含 cannbot-skills submodule）
git clone --recurse-submodules <repo-url>
cd ascend_op_agent

# 安装（建议 conda 环境）
pip install -e .
```

### 配置

Ascend Op Agent 采用**双文件配置**：

| 文件 | 内容 | 提交 |
|------|------|------|
| `~/.ascend_op_agent/config.yaml` | 行为配置（LLM、910B 远程、checkpoint） | ❌ 不提交 |
| `~/.ascend_op_agent/.env` | 敏感凭据（API key、SSH 密码） | ❌ 不提交 |

```bash
mkdir -p ~/.ascend_op_agent
cp config.example.yaml ~/.ascend_op_agent/config.yaml
cp .env.example ~/.ascend_op_agent/.env
chmod 600 ~/.ascend_op_agent/.env
# 编辑 .env 填 API key（MINIMAX_API_KEY / GLM_API_KEY / ARK_API_KEY）
vim ~/.ascend_op_agent/.env
```

`config.yaml` 关键段（默认主力 Minimax，三 provider 轮询，全 Anthropic 兼容）：

```yaml
llm:
  provider: "anthropic"                          # 三 provider 均走 anthropic 兼容端点
  api_key: "${MINIMAX_API_KEY}"
  api_base: "https://api.minimaxi.com/anthropic"
  model: "MiniMax-M3"
  timeout: 300                                   # 推理模型 reasoning 阶段长，加大 timeout

remote:                                          # 910B 容器拓扑
  host: "192.168.9.105"
  user: "root"
  container_name: "ops_pt"                       # NpuExecutor 自动包装 docker exec ops_pt

checkpoint:
  db_path: ~/.ascend_op_agent/checkpoints.db
  auto_resume: true                              # 进程重启自动从断点续跑
```

> 切换备 1 智谱 GLM-5.2 / 备 2 火山 Ark GLM-5.2 只需改 `llm` 段，详见 [配置指南](docs/config.md) 与 [LLM Provider](docs/modules/llm_providers.md)。

### 910B 远程环境

算子编译/验证在远程 910B 执行，本地编排：

```
本地 Mac
  └─ ssh root@192.168.9.105（免密）
       └─ docker exec ops_pt（常驻开发容器）
            ├─ CANN 9.1.0（aarch64）
            └─ NPU: 910B3 × 8 卡（64G HBM/卡）
```

> **坑**：非交互 shell 不自动 source CANN env，`NpuExecutor` 内部已前置 `source /usr/local/Ascend/ascend-toolkit/set_env.sh`，无需手动处理。

## 使用样例

### 样例 1：单算子全新开发（路径 C）

```bash
# op: 前缀触发 PhaseRunner 编排
ascend-op-agent run "op: 实现一个向量加法算子，输入两个 half tensor，输出逐元素相加"
```

Agent 自主执行全流程，仅在 HITL 节点暂停等你确认：

```
entry → analyze（产 OpInfo）
      → design（HITL：等你确认 Tiling 策略与 API 映射）
      → codegen（生成 kernel + host + proto）
      → review_fix（审查代码）
      → compile（910B 真编译 build.sh --soc=ascend910b）
      → precision（ST 驱动：NPU 跑 + CPU golden + MERE/MARE 比对）
      → delivery_mode（HITL：选 sample / torch_npu / pybind 交付模式）
      → done
```

### 样例 2：单算子迁移（路径 B）

```bash
# CUDA 算子迁移到 AscendC
ascend-op-agent run "op: 把这段 CUDA 算子迁移到 AscendC：<粘贴 CUDA 代码>"

# Triton 算子迁移
ascend-op-agent run "op: 迁移这个 Triton kernel：<粘贴 triton 代码>"
```

### 样例 3：交互式 TUI 对话

```bash
ascend-op-agent run          # 双进程 TUI（Ink + React 前端 ↔ Python 后端，JSON-RPC）
```

### 样例 4：可视化调试

```bash
ascend-op-agent viewer       # 对话可视化器（Vue3 + Element Plus 前端 ↔ FastAPI 后端）
                              # 查看会话树、checkpoint 状态、skill 使用记录
ascend-op-agent viewer --only-backend   # 仅启动后端 API
```

### 样例 5：IDE 集成

```bash
ascend-op-agent acp          # ACP 协议 stdio 模式，对接 VS Code / Zed / JetBrains
```

### 验证：ship gate（质量门控）

```bash
PYTHONPATH=src python scripts/ship_ready.py
# lint ✅ → unit_test ✅ → stress(N=20) ✅ → e2e_tui ✅ → 🚢 SHIP READY

PYTHONPATH=src python scripts/ship_ready.py --skip-stress   # dev fast（跳 stress）
```

## CLI 命令参考

| 命令 | 说明 |
|------|------|
| `ascend-op-agent init` | 初始化项目配置和工作目录 |
| `ascend-op-agent run [input]` | 启动 Agent 对话；`op:` 前缀触发算子开发编排 |
| `ascend-op-agent skill list/install/remove/add` | Skill 知识库管理 |
| `ascend-op-agent mcp list/start/stop/status` | MCP 服务器管理 |
| `ascend-op-agent sync push/pull -f <files>` | 本地 ↔ 远程文件同步 |
| `ascend-op-agent viewer` | 对话可视化器（`--only-backend` / `--port`） |
| `ascend-op-agent acp` | ACP 编辑器集成模式（stdio） |
| `ascend-op-agent task new/list/select/progress` | 任务管理层（多任务并行，`task_type ∈ migrate/analyze/optimize/develop`） |

## 文档

| 文档 | 内容 |
|------|------|
| [配置指南](docs/config.md) | 双文件配置 + 三 provider 轮询 + streaming |
| [架构设计](docs/architecture.md) | 六层架构 + PhaseRunner + 记忆系统 |
| [工作流引擎](docs/workflow.md) | PhaseRunner 状态机 + 三路径 + fix_loop |
| [开发指南](docs/development.md) | 项目结构 + 开发环境 + 调试 |
| [TUI 指南](docs/tui-guide.md) | 双进程 TUI 交互 |
| [模块文档](docs/modules/) | 核心模块详解（orchestrator / llm_providers / skills / ssh / memory / mcp / acp） |
| [变更日志](CHANGELOG.md) | 版本历史 |
| [安全策略](SECURITY.md) | 凭据管理 |

## 开发

```bash
pip install -e ".[dev]"                             # 安装开发依赖
PYTHONPATH=src python -m pytest tests/ -v           # 测试（426 passed）
black src/ tests/ && ruff check --fix src/ tests/   # 格式化
```

详细开发指南见 [docs/development.md](docs/development.md)。

## License

Apache License 2.0 · Copyright 2026 SimmerChan
