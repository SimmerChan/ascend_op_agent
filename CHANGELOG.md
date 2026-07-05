# 变更日志

<!--
Copyright 2026 SimmerChan

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

所有值得注意的更改都将记录在此文件中。

---

## [v1.0.0] — 2026-07-06

**Ascend Op Agent v1.0** — 算子迁移/开发运行时引擎。自研轻量状态机编排器 + cannbot-skills 知识层 + 910B 真编译 + ST 驱动真算子验证。

### 核心成就

| 维度 | 结果 |
|------|------|
| P0 plan（14U + 9U gap 修复） | ✅ 全完成，5 gap all satisfied |
| P1 plan（8U） | ✅ 全完成（schema v2 + vitest + stress 100% + CLI 端到端 + SIGTERM + ship gate） |
| Ship gate | ✅ 4 步全过 → 🚢 SHIP READY |
| N=20 stress | ✅ 20/20 = 100% CLEAN PASS（Minimax） |
| Commit 总数 | 80+ commits（2026-06-23 → 2026-07-06，14 天） |
| 测试总数 | 426 passed + 4 skipped + 2 vitest |

### 新增功能

- **自研 PhaseRunner 状态机**：14 节点顺序+条件+HITL（entry → analyze → design(HITL) → codegen → review_fix → compile → precision → delivery_mode → framework_adapt → done）
- **CheckpointStore**：SQLite v2 schema + v1↔v2 migration + rollback + quarantine LRU + WAL + busy_timeout
- **NpuExecutor**：SSH → docker exec ops_pt → build.sh --soc=ascend910b + ST 驱动（NPU 跑 + CPU golden + MERE/MARE）
- **compile cosmetic fix**：build.sh 末尾 `[ERROR] Package not found` false negative → `_is_compile_success` 检测 `.run successfully created`
- **tool_calls_log side-channel**：LLM `file_write` 实际路径可达编排器（Gap 1 修复）
- **fix_loop messages 累积**：`apply_update` module-level reducer 修跨轮 conversation 丢失（Gap 3 修复）
- **SkillUsageRegistry**：signal-1 跟踪 cannbot skill 加载/使用 + phase_callback 推前端
- **ship_ready.py**：单一 boolean gate（lint + unit_test + stress + e2e_tui → 0/1 exit）
- **LLM 多 Provider**：Minimax（anthropic 协议，20/20 stress）+ 智谱 GLM-5.2（OpenAI 兼容 + coding plan token）
- **adapter 错误改进**：`None` → `type+message` + quota 快速失败不重试
- **backend lazy orchestrator**：普通 CLI `run` 不带 op: 启动快 ~3s
- **SIGTERM graceful + heartbeat**：`loop.add_signal_handler` + 30s alive event
- **frontend vitest + ink-testing-library**：2 Ink render test + parseProgress 8 单测

### 技术债清理

| 项 | 修复 |
|----|------|
| D1 log noise | `send_notification_sync` fire-and-forget `TimeoutError` 不 log |
| D2 lazy orchestrator | 普通 CLI `run` 不实例化 Orchestrator（省 ~3s） |
| D3 pytest-asyncio | test_server.py 15 async 测从 skip 变 pass |

### 已知限制

- GLM-5.2 stress：推理模型连续 API timeout（1/20 = 5%），不适合 N=20
- Minimax quota：N=20 消耗当天额度（60-80 次 LLM 调用）
- A5/950 未验证：当前仅 910B
- 模型级批量迁移（路径 A）：P2 未实施

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

### 变更

### 废弃

### 修复

### 安全

## [0.1.0] - 2026-05-04

### 首次发布

#### 核心功能

- **六阶段工作流引擎**: Phase0-5 自动完成算子开发
  - Phase0: 初始化 - 环境检测和设置
  - Phase1: 需求分析 - 自动分析算子需求
  - Phase2: 方案设计 - 架构和 tiling 策略设计
  - Phase3: 代码生成 - 生成 AscendC/CATLASS/Triton 代码
  - Phase4: 编译验证 - 自动编译和修复错误
  - Phase5: 精度评估 - 验证精度 (≥30 测试用例)

- **双进程 TUI 交互界面**: 基于 JSON-RPC 的前后端分离架构
  - Node.js + Ink 前端
  - Python Agent 后端
  - stdin/stdout 进程通信

- **SSH 远程开发支持**: 远程 Ascend 服务器开发
  - SSHManager 连接管理
  - 文件同步 (rsync 风格)
  - 远程命令执行

- **MCP 服务器集成**: 外部工具扩展
  - stdio、HTTP、streamable-http 模式支持
  - OAuth 认证支持
  - 生命周期管理

- **四层记忆系统**:
  - Working Memory: 当前会话上下文
  - Episodic Memory: 完整会话历史 (ChromaDB)
  - Semantic Memory: Skill 知识向量
  - Procedural Memory: 场景化工作流模板

- **Skill 知识库**: 算子开发经验积累和复用
  - 模板、Bugfix、性能优化维度
  - SQLite FTS5 全文检索
  - 混合检索 (向量 + 关键词)

- **ACP 编辑器适配器**: 支持主流 IDE
  - VS Code
  - Zed
  - JetBrains

- **双文件配置架构**: config.yaml + .env 分离
  - 环境变量引用支持
  - 默认值语法
  - 安全凭据隔离

#### 项目结构

```
ascend_op_agent/
├── agent/           # Agent 核心引擎
│   ├── core.py      # AIAgent 主类
│   ├── memory.py    # 记忆系统
│   ├── context.py   # 上下文引擎
│   ├── prompt_builder.py  # 7层 Prompt 组装
│   └── tool_registry.py   # 工具注册
├── workflow/       # 工作流引擎
│   ├── engine.py    # 工作流引擎
│   ├── phases.py    # 阶段定义
│   ├── adapters.py  # 适配器
│   └── models.py    # 数据模型
├── mcp/            # MCP 服务器集成
│   ├── client.py    # MCP 客户端
│   ├── lifecycle.py # 生命周期管理
│   └── oauth.py    # OAuth 认证
├── skills/         # Skill 知识库
│   ├── repository.py # 技能仓库
│   ├── index.py     # 技能索引
│   └── installer.py  # 技能安装
├── ssh/            # SSH 远程开发
│   ├── manager.py   # SSH 管理器
│   ├── sync.py     # 文件同步
│   └── env_config.py # 环境配置
├── memory/        # 记忆系统
│   ├── episodic_memory.py  # 情景记忆
│   ├── semantic_memory.py  # 语义记忆
│   └── vector_store.py     # 向量存储
├── acp/           # ACP 编辑器适配器
│   ├── adapter.py  # 适配器主类
│   ├── protocol.py # 协议定义
│   └── session.py  # 会话管理
├── backend/       # RPC 后端服务
│   └── rpc/       # JSON-RPC 协议
├── security/      # 安全模块
│   ├── credential_manager.py # 凭据管理
│   └── token_resolver.py    # Token 解析
└── cli.py         # CLI 入口
```

#### 依赖

**核心依赖:**
- click >= 8.1.0
- rich >= 13.0.0
- pydantic >= 2.0.0
- pyyaml >= 6.0.0
- paramiko >= 3.0.0
- mcp >= 1.0.0
- chromadb >= 0.4.0
- sentence-transformers >= 2.2.0
- python-dotenv >= 1.0.0

**开发依赖:**
- pytest >= 8.0.0
- pytest-asyncio >= 0.23.0
- pytest-mock >= 3.12.0
- black >= 24.0.0
- ruff >= 0.3.0
- mypy >= 1.9.0

[Unreleased]: https://github.com/simmerchan/ascend-op-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/simmerchan/ascend-op-agent/releases/tag/v0.1.0
