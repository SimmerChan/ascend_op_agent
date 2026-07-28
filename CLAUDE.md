# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Ascend Op Agent — 昇腾算子开发 Agent，支持六阶段自动化工作流（Phase0-5）、双进程 TUI 交互界面、SSH 远程开发和 Skill 知识库。

**P0 + P1 已完成**（2026-06-23 → 2026-07-05）：自研状态机编排器 + cannbot-skills 知识层 + 910B 真编译 + ST 驱动真算子验证 + N=20 stress 100% PASS + CLI 端到端 5/5 PASS + ship gate 4 步全过。

## 常用命令

```bash
# 安装（项目根目录）
pip install -e .

# 切换环境
conda activate py311

# Ship gate（P1 唯一 boolean gate）
PYTHONPATH=src python scripts/ship_ready.py                    # 全跑（需 910B + LLM）
PYTHONPATH=src python scripts/ship_ready.py --skip-stress      # dev fast（跳 stress）
PYTHONPATH=src python scripts/ship_ready.py --only unit_test   # 只跑 1 步

# N=20 stress（P1 真稳定性验证）
PYTHONPATH=src python scripts/e2e_real_op.py --stress 20       # 真跑 ~40 min（Minimax）
PYTHONPATH=src python scripts/e2e_real_op.py --stress 5        # CI smoke（快速）

# CLI 端到端（P1 U4: spawn backend + stdin RPC）
PYTHONPATH=src python scripts/e2e_tui_real.py --timeout 300    # 真跑 op: 前缀

# 单次 e2e（P0 reference migration: scaffold → compile → precision）
PYTHONPATH=src python scripts/e2e_real_op.py                   # 单次跑通 add_example

# Python 测试
PYTHONPATH=src python -m pytest tests/ -v
PYTHONPATH=src python -m pytest tests/test_agent.py -v  # 单文件

# 前端测试（P1 U2: vitest + ink-testing-library）
cd frontend && npm test                                        # 2/2 vitest pass

# 代码格式化和检查
black src/ tests/
ruff check --fix src/ tests/

# TUI 前端（Node.js）
cd frontend && npm install && npm run dev

# Viewer 可视化工具（双进程）
ascend-op-agent viewer          # 前端(port 3001) + 后端(port 3002)
ascend-op-agent viewer --only-backend  # 仅后端
```

### Agent 工具集

Agent 提供以下工具（定义在 `agent/tools/`）：

| 工具 | 说明 |
|------|------|
| `file_read` | 读取文件内容（带大小限制、loop 检测） |
| `file_write` | 写入文件（原子写入、备份） |
| `file_search` | 正则/glob 搜索文件内容或名称 |
| `patch` | 文本替换（精确/模糊匹配） |
| `shell_exec` | 执行 Shell 命令 |
| `python_exec` | 执行 Python 脚本（子进程隔离） |
| `git_log` | 查询 Git 提交历史 |
| `git_diff` | 查看 Git 差异 |
| `git_status` | 查看 Git 仓库状态 |
| `git_branch` | 列出 Git 分支 |
| `npu_smi` | 查询昇腾 NPU 设备信息 |
| `msop` | CANN 算子分析工具 |
| `cann_compile` | CANN 算子编译 |

## 系统架构

### 双进程 TUI 架构

```
┌─────────────────────────────────────────────────────┐
│  TUI Frontend (Node.js + Ink + React)             │
│  端口 3002 | 进程间通过 stdio JSON-RPC 通信         │
│  状态: idle/running/waiting_confirm/completed/error │
└───────────────────────┬─────────────────────────────┘
                        │ stdin/stdout (JSON-RPC 2.0)
                        ▼
┌─────────────────────────────────────────────────────┐
│  Agent Backend (Python)                            │
│  ThreadPoolExecutor 运行 AIAgent                   │
│  响应 agent.progress 通知更新进度条                  │
│  响应 agent.thinking 通知更新 thinking 状态          │
└─────────────────────────────────────────────────────┘
```

### 编排器架构（P0 + P1 已完成）

```
┌─────────────────────────────────────────────────────────────────────┐
│  Orchestrator (PhaseRunner)                                         │
│  src/ascend_op_agent/orchestrator/                                  │
│                                                                     │
│  PhaseRunner (state_machine.py)                                     │
│  ├── entry → analyze → design(HITL) → codegen → review_fix          │
│  ├── compile(cosmetic fix) → precision(ST driver) → delivery_mode    │
│  └── framework_adapt → done                                         │
│                                                                     │
│  CheckpointStore (checkpoint.py) — SQLite v2 + v1↔v2 migration      │
│  NpuExecutor (npu_exec.py) — SSH→910B build.sh + ST driver          │
│  SkillUsageRegistry (cannbot_loader.py) — signal-1 skill 跟踪       │
│  fix_loop (fix_loop.py) — review→fix→re-review 闭环                 │
└─────────────────────────────────────────────────────────────────────┘

ship gate (scripts/ship_ready.py):
  lint(black) → unit_test(pytest 411 pass) → stress(N=20 100%) → e2e_tui(5/5)
  → 🚢 SHIP READY (exit 0)
```

### Viewer 可视化工具（独立架构）

```
┌──────────────────┐     ┌──────────────────────────┐
│ Vue 3 Frontend   │────▶│ FastAPI Backend (3001)    │
│ (端口 3001)      │     │ /api/sessions/{id}/tree   │
│                  │     │ 会话记录树形结构 API       │
└──────────────────┘     └──────────────────────────┘
        │                         ▲
        │        Vite 代理         │
        ▼                         │
┌──────────────────┐              │
│ Vite Dev Server  │──────────────┘
│ (端口 3002)      │  /api/* 代理到 3001
└──────────────────┘
```

### 核心模块依赖

```
agent/core.py (AIAgent)
  ├── agent/prompt_builder.py  (7层Prompt组装)
  ├── agent/tool_registry.py    (工具注册)
  ├── agent/memory.py           (四层记忆)
  └── agent/context.py          (上下文引擎)

backend/rpc/agent_service.py (AgentAsyncWrapper)
  └── agent/core.py            (同步执行封装)

backend/rpc/server.py (JSONRPCServer)
  └── send_notification(method, params)  → stdout
```

### Agent 执行流程与通知机制

`AIAgent.run_conversation()` 是同步循环，每个迭代：
1. 组装 7 层 Prompt
2. 调用 LLM（`LLMClient.call()`）
3. 解析工具调用（如有）
4. 执行工具（`_execute_tool_call()`）
5. 追加到对话历史

**前端期望的进度通知**（定义在 `frontend/src/hooks/useRPC.ts`）：
- `backend.ready` — 后端就绪
- `agent.thinking` — LLM 思考中
- `agent.progress` — `{ phase: number, percent: number }`
- `agent.error` — 执行出错

当前 `AIAgent` **不发送这些通知**，只有最终响应返回后才切换状态。

### 配置：双文件架构

| 文件 | 内容 | 版本控制 |
|------|------|----------|
| `config.yaml` | LLM 模型、MCP 服务器等 | ✅ 提交 |
| `~/.ascend_op_agent/.env` | API 密钥等敏感信息 | ❌ 不提交 |

## 目录结构

```
src/ascend_op_agent/
├── agent/          # 核心引擎
│   ├── core.py      # AIAgent 主类
│   ├── SOUL.md      # Agent 身份定义
│   ├── memory.py    # Working Memory
│   └── providers/   # 多 Provider LLM 适配器
├── backend/rpc/    # JSON-RPC 服务端
│   ├── server.py    # JSONRPCServer（send_notification）
│   └── agent_service.py  # AgentAsyncWrapper
├── workflow/       # Phase0-5 工作流引擎
├── mcp/            # MCP 客户端
├── skills/         # Skill 知识库
├── memory/          # Episodic/Semantic Memory (ChromaDB)
├── ssh/            # SSH 远程开发
├── acp/            # ACP 编辑器适配器（stdio 协议）
└── viewer/         # Agent 对话可视化器（独立服务）
```

## 关键设计

### LLM Provider 适配器模式

`LLMClient` 使用注册表模式：`{"openai": OpenAIAdapter, "anthropic": AnthropicAdapter, ...}`。新增 Provider 只需添加适配器类，无需修改核心逻辑。

### 工具调用双模式

Agent 支持两种工具调用格式：
1. **Native Function Calling** — 结构化 `ToolCallResult`
2. **XML 格式兼容** — `<tool_call name="xxx">{...}</tool_call>`

### 会话记录

`SessionRecordManager` 持久化会话（LLMEntry、ToolEntry、UserEntry 等），支持树形结构查询。数据存储在 `~/.ascend_op_agent/sessions/`。

## 环境要求

- Python >= 3.10
- Node.js >= 16（TUI 前端）
- ChromaDB（向量存储，记忆系统）

## 910B 远程开发环境（192.168.9.105）

算子编译/验证的实际硬件。本地编排，远程编译。

### 拓扑

```
本地 Mac (darwin)
  └─ ssh root@192.168.9.105         # 已配免密登录
       └─ server105 宿主机
            └─ docker exec ops_pt    # 开发容器
                 ├─ 工作目录: /home/hsl/ops_agent
                 ├─ CANN: cann-9.1.0 (aarch64)
                 └─ NPU: 910B3 × 8 卡
```

### 关键事实（2026-06-25 实测）

| 项 | 值 |
|------|------|
| SSH 登录 | `ssh root@192.168.9.105`（免密） |
| 宿主机 hostname | server105 |
| 开发容器 | `ops_pt`（docker，常驻） |
| 容器工作目录 | `/home/hsl/ops_agent` |
| CANN 版本 | cann-9.1.0，innerversion V100R001C25B114 |
| set_env.sh | `/usr/local/Ascend/ascend-toolkit/set_env.sh` |
| 芯片 | 910B3（8 卡，64G HBM/卡），soc_version=`Ascend910B3` |
| 架构 | aarch64（容器与芯片原生 ARM） |

### 算子编译命令（重要）

**CANN 9.1.0 没有 `cann_compile` 二进制** —— `agent/tools/npu_tool.py` 和早期
`NpuExecutor` 假设的 `cann_compile -target npu` 命令在此环境不存在。真实编译流程：

```bash
# 在容器内（每次 docker exec 是新 session，必须先 source）
docker exec ops_pt bash -c "
  source /usr/local/Ascend/ascend-toolkit/set_env.sh &&
  msopgen compile -i <operator_project_dir> -q
"
```

- `msopgen gen`：从算子定义生成工程骨架（CMakeLists + op_host/op_kernel）
- `msopgen compile -i <project> -q`：编译算子工程（替代假设的 cann_compile）
- `msopgen sim`：仿真运行

### SSH 进入容器执行命令的模板

```bash
# 只读探查
ssh root@192.168.9.105 'docker exec ops_pt bash -c "
  source /usr/local/Ascend/ascend-toolkit/set_env.sh > /dev/null 2>&1;
  <命令>
"'

# 看芯片状态
ssh root@192.168.9.105 'docker exec ops_pt bash -c "
  source /usr/local/Ascend/ascend-toolkit/set_env.sh;
  npu-smi info
"'
```

### 坑：非交互 shell 不加载 CANN env

`bash -lc` / `docker exec` 默认非交互，**不会自动 source `set_env.sh`**，导致
`ASCEND_OPP_PATH` 空、`msopgen`/`cann_compile` 不在 PATH。任何远程命令必须显式
`source /usr/local/Ascend/ascend-toolkit/set_env.sh &&` 前置。

`NpuExecutor(remote_env_setup="source .../set_env.sh && ")` 即为此设计。

### 硬件冒烟测试

环境变量驱动的 hardware-gated 测试（本地无 env 自动 skip）：

```bash
# 配置（写 ~/.ascend_op_agent/.env）
NPU_HOST=192.168.9.105
NPU_USER=root
NPU_CANN_SETUP=/usr/local/Ascend/ascend-toolkit/set_env.sh
# 容器拓扑需额外包装 docker exec（见 NpuExecutor 集成说明）

pytest -m hardware tests/integration/test_ssh_compile_smoke.py -v
```

> 注：当前 `test_ssh_compile_smoke.py` 假设直接 SSH 进开发环境，910B 的
> 容器拓扑（ssh→server105→docker exec ops_pt）已在 NpuExecutor 中实现
> （`container_name="ops_pt"` 参数自动包装 `docker exec`）。P0 U13 + P1 U1 已完成。

### 真实算子编译 + 验证流程（P0 + P1 已验证）

**单次 e2e（reference migration 路径，~2 min Minimax）**：

```bash
# 1. 拉 scaffold（只一次）
ssh root@192.168.9.105 'docker exec ops_pt bash -c "cd /tmp/op_test && tar -cf - --exclude=build ..."' \
  > /tmp/e2e_scaffold.tar && tar -xf /tmp/e2e_scaffold.tar -C /tmp/e2e_scaffold

# 2. 跑 e2e
PYTHONPATH=src python scripts/e2e_real_op.py
# 预期: compile success=True + precision 10/10 PASS + done
```

**N=20 stress（P1 ship gate stress step）**：

```bash
PYTHONPATH=src python scripts/e2e_real_op.py --stress 20
# Minimax: 20/20 = 100% CLEAN PASS (~40 min)
# GLM-5.2: API 连续调用 timeout（推理模型不适合 stress）
```

**compile cosmetic fix**：build.sh 末尾 `[ERROR] Package not found or empty` 是
已知 false negative（return_code=1 但 .run 产物实际已生成）。`NpuExecutor._is_compile_success`
检测 stdout 含 `successfully created` + `.run` → 标 success=True。

## LLM 切换规则（Minimax ↔ GLM ↔ Ark 三 provider 轮询）

LLM 配额/服务频繁踩坑，已配三个 provider 互备。调试时任一 provider 不可用（配额耗尽 / 服务波动 / timeout），按轮询顺序切下一个 provider 重试，不原地重试同一 provider。

**轮询顺序（默认）**：Minimax（主力）→ GLM-5.2（备 1）→ Ark GLM-5.2（备 2）→ 三者都挂则停止 LLM 依赖操作并报告用户。

| Provider | Model | api_base | Protocol | Key env | 适用 / 备注 |
|---------|-------|----------|----------|---------|------|
| **Minimax MiniMax-M3**（默认主力）| `MiniMax-M3` | `https://api.minimaxi.com/anthropic` | anthropic | `MINIMAX_API_KEY` | e2e_real_op / ship_ready 验证;N=20 stress 100% PASS |
| **智谱 GLM-5.2**（备 1）| `glm-5.2` | `https://open.bigmodel.cn/api/anthropic` | anthropic | `GLM_API_KEY` | Minimax 配额耗尽时切;/api/anthropic 是 Anthropic 兼容端点(coding/paas/v4 实测 404,2026-07-14 纠正);不适合 stress(连续调用 timeout) |
| **火山 Ark GLM-5.2**（备 2）| `glm-5.2` | `https://ark.cn-beijing.volces.com/api/plan` | anthropic | `ARK_API_KEY` | Minimax+GLM 都不可用时切;同模型 glm-5.2 走火山引擎;Anthropic 兼容(SDK 拼 `/v1/messages`) |

**切换方法**（改 `~/.ascend_op_agent/config.yaml` 的 `llm` 段；三 provider 的 key 已在 `~/.ascend_op_agent/.env` 配齐：`MINIMAX_API_KEY` / `GLM_API_KEY` / `ARK_API_KEY`）：

```yaml
# Minimax (默认主力, Anthropic 兼容)
llm:
  provider: "anthropic"
  api_key: "${MINIMAX_API_KEY}"
  api_base: "https://api.minimaxi.com/anthropic"
  model: "MiniMax-M3"

# GLM-5.2 (备 1, Anthropic 兼容)  — 取消注释切换
# llm:
#   provider: "anthropic"
#   api_key: "${GLM_API_KEY}"
#   api_base: "https://open.bigmodel.cn/api/anthropic"
#   model: "glm-5.2"
#   disable_thinking: true  # glm-5.2 推理模型,禁 thinking 否则吃光 max_tokens(4096) 致 codegen text 空(见经验)

# Ark GLM-5.2 (备 2, Anthropic 兼容)  — 取消注释切换
# llm:
#   provider: "anthropic"
#   auth_token: "${ARK_API_KEY}"   # Ark 走 Bearer(adapter 已支持 auth_token);api_key 的 x-api-key 会 401
#   api_base: "https://ark.cn-beijing.volces.com/api/plan"
#   model: "glm-5.2"
#   disable_thinking: true  # glm-5.2 推理模型,禁 thinking 否则吃光 max_tokens(4096) 致 codegen text 空
```

**轮询调试策略（Claude 执行）**：
- 调试中遇 provider 报错（402/403 配额、连接 timeout、5xx 服务波动）→ 不原地重试，按轮询顺序切下一个 provider（改 config.yaml 的 `llm` 段）后重试
- 切 provider 后先用 `--skip-stress` 或单次 e2e 验证连通性，再跑重任务（stress / ship gate）
- 三 provider 都不可用 → 停止 LLM 依赖操作，报告用户
- **harness 分类器独立**：Claude Code 自身的权限分类器也用 glm-5.2（见 `~/.claude/settings.json` 的 `ANTHROPIC_BASE_URL`），与项目 provider 独立；分类器报 "glm-5.2 temporarily unavailable" 是 harness 层故障，切项目 provider 不解决，需等 harness 服务恢复或用户手动执行命令

**经验**：
- **默认 Minimax**：N=20 stress 100% PASS（line 24-25）
- **GLM-5.2 不适合 stress**：连续调用 timeout（line 324），仅作 spike 一次性 codegen 验证
- **glm-5.2 thinking 吃光 max_tokens（2026-07-24 坐实）**：glm-5.2 是**推理模型**，SOUL system_prompt 触发 thinking 膨胀 12-15k 字符，吃光 adapter `max_tokens=4096`（`stop_reason=max_tokens`），visible text 输出 **0 字符** → codegen kernel/host 全空 → compile 缺 object file。修复：config 加 `disable_thinking: true`（adapter `complete()` 传 `thinking={type:disabled}`）。**Minimax M3 非推理模型保持不设**（其兼容端点可能不认 thinking 参数）。**教训**：LLM 返回空必先打 raw response（`stop_reason`/`content blocks` thinking vs text/`usage.output_tokens`），别瞎归因 prompt 信噪比或 LLM 能力
- **Ark auth_token**：Ark 走 Bearer auth，adapter `auth_token` 参数已支持（`api_key` 走 x-api-key 会 401），config.yaml 用 `auth_token: "${ARK_API_KEY}"`；`/api/plan` 是 Anthropic 兼容端点（SDK 拼 `/api/plan/v1/messages`）
- 切 GLM 后跑 spike 5/5/6/7 真实发现 add_custom 参考工程与 910B CANN 9.1.0 不兼容（spike #6 暴露第 5 层根因），U2 加 `inline_build_template` 内联 `add_example` 修复

**`render_skill_bundle_text` 内联构建参考**（U2，commit 38be32d）：
- codegen 阶段从 `ascendc-registry-invoke-template/references/add_example/` 内联 `CMakeLists.txt + build.sh`
- 解决 LLM 写 build.sh 漏 `ASCEND_COMPUTE_UNIT` / `arch22/arch35` 分代（CANN 9.1.0 legacy_modules/host_config.cmake 期望）
- add_custom（ascendc-direct-invoke-template/references/）跟 910B 不兼容，spike #6 证伪后改用 add_example

## 调试

```bash
# 查看详细日志
ascend-op-agent run --debug

# TUI 前端卡住时检查后端进程
ps aux | grep ascend_op_agent
lsof -i :3002
```

## 方案设计参考

Ascend Op Agent任何特性方案在设计前，都必须先参考以下方案的设计。

1. hermes agent源码目录: /Users/huangshilei/Documents/pythonprojects/hermes-agent
   1. 架构说明wiki文档： /Users/huangshilei/Documents/pythonprojects/hermes-agent/.zread/
2. claude code源码目录: /Users/huangshilei/Documents/pythonprojects/claude-code
   1. 架构说明wiki文档： /Users/huangshilei/Documents/pythonprojects/claude-code/.zread/