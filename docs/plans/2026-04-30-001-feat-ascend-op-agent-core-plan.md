---
title: feat: Ascend Op Agent Core System
type: feat
status: active
date: 2026-04-30
origin: docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md
---

# Ascend Op Agent 核心系统实现规划

## Overview

构建昇腾算子开发Agent核心系统，支持从0开发算子和GPU迁移两种场景，兼容本地/远程开发模式，集成MCP服务器和Skill知识库系统。

## Problem Frame

昇腾算子开发者当前面临：
- 手工开发效率低，重复工作多
- GPU迁移缺乏自动化工具
- 经验难以积累和复用
- 环境配置复杂（本地vs远程）

本系统通过自动化工作流和技能复用提升开发效率。

## Requirements Trace

| ID | 需求 | 来源 |
|----|------|------|
| R1 | 会话初始化（本地/远程模式） | KD2 |
| R2 | 需求分析阶段（自动） | KD1 |
| R3 | 方案设计阶段（用户确认） | KD1 |
| R4 | 代码生成阶段 | KD3 |
| R5 | 编译验证（自动修复最多3次） | KD3 |
| R6 | 框架适配（可选） | KD5 |
| R7 | 技能保存（可选） | KD4 |
| R8 | MCP服务器集成 | KD6 |
| R9 | Skill仓库管理 | KD7 |

## Scope Boundaries

### In Scope
- Agent核心引擎（会话管理、Prompt组装、工具注册）
- 本地开发模式
- 远程开发模式（SSH）
- MCP服务器集成（stdio/HTTP）
- Skill仓库管理（git/local）
- 六阶段工作流（R1-R6，R7-R9为并行能力）

### Out of Scope
- 算子融合优化
- 分布式算子
- CANN版本适配（假设环境已就绪）
- 硬件故障排查

## Key Technical Decisions

### KD-1: 基于Hermes Agent简化架构
**决策**: 复用Hermes Agent的ToolRegistry自注册、7层Prompt Assembly、Skill格式
**理由**: 已验证的工程实践，减少重复造轮子

### KD-2: SSH连接使用paramiko库
**决策**: 远程模式采用paramiko实现SSH连接和文件传输
**理由**: 纯Python实现，与项目技术栈一致；支持增量rsync

### KD-3: Skill存储采用SQLite+全文搜索
**决策**: 本地Skill索引使用SQLite FTS5
**理由**: 轻量、无外部依赖、支持语义检索扩展

### KD-4: MCP使用mcp-sdk Python客户端
**决策**: MCP集成采用官方mcp-sdk
**理由**: 官方支持stdio和HTTP模式，与LangChain/MCP生态兼容

### KD-5: 代码修复限定为确定性修复
**决策**: 仅自动修复语法错误、拼写错误、缺失头文件、类型不匹配
**理由**: 逻辑错误超出LLM可靠修复范围

## Open Questions

### Resolved During Planning

| 问题 | 解决方案 |
|------|----------|
| SSH连接管理 | 使用paramiko，指数退避重连3次 |
| 文件同步策略 | rsync增量同步，rsync --checksum验证 |
| 代码修复AI策略 | 确定性修复+用户确认的混合策略 |
| 技能存储格式 | SQLite FTS5 + YAML元数据 |
| 性能基准测试 | 输出原始数据，由用户自行判断达标 |

### Deferred to Implementation

- 特定AscendC/CATLASS算子生成策略（R3-R4阶段）
- PyTorch/TensorFlow适配器实现（R6阶段）
- Skill自动注入的匹配算法（R7阶段）

## High-Level Technical Design

```
┌─────────────────────────────────────────────────────────────┐
│                      ascend_op_agent                          │
├─────────────────────────────────────────────────────────────┤
│  CLI层 (click + rich)                                        │
│    └── 命令: init, run, skill, mcp, sync                     │
├─────────────────────────────────────────────────────────────┤
│  Agent核心层                                                  │
│    ├── AIAgent: 会话管理、迭代控制                           │
│    ├── PromptBuilder: 7层Prompt组装                          │
│    ├── ContextEngine: 上下文压缩和检索                        │
│    └── ToolRegistry: 自注册工具系统                          │
├─────────────────────────────────────────────────────────────┤
│  工作流层 (OperatorWorkflow)                                 │
│    ├── Phase0: 初始化（环境检测）                            │
│    ├── Phase1: 需求分析（自动）                              │
│    ├── Phase2: 方案设计（用户确认）                          │
│    ├── Phase3: 代码生成                                      │
│    ├── Phase4: 编译验证                                      │
│    ├── Phase5: 框架适配（可选）                              │
│    └── Phase6: 技能保存（可选）                              │
├─────────────────────────────────────────────────────────────┤
│  集成层                                                      │
│    ├── SSHManager: paramiko SSH + rsync                      │
│    ├── MCPClient: mcp-sdk stdio/HTTP                        │
│    ├── SkillRepository: git sync + SQLite索引                │
│    └── ConfigManager: YAML配置                               │
├─────────────────────────────────────────────────────────────┤
│  工具层 (内置)                                               │
│    ├── file_ops: 读写、目录操作                               │
│    ├── shell_ops: 执行命令                                   │
│    ├── ssh_ops: 远程命令/文件传输                            │
│    ├── ascend_ops: CANN环境检测、编译                         │
│    └── skill_ops: 技能CRUD                                   │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Units

- [ ] **Unit 1: 项目脚手架**

**Goal:** 建立项目基础结构和配置

**Requirements:** R1, KD2

**Dependencies:** None

**Files:**
- Create: `pyproject.toml`
- Create: `config.yaml.example`
- Create: `src/ascend_op_agent/__init__.py`
- Create: `src/ascend_op_agent/cli.py`
- Create: `src/ascend_op_agent/config.py`
- Create: `src/ascend_op_agent/version.py`
- Create: `tests/test_config.py`
- Create: `tests/test_cli.py`

**Approach:**
- pyproject.toml: click, rich, pydantic, pyyaml, sqlparse, fts5
- config.yaml.example: 包含所有配置项及注释
- CLI使用click + rich构建，支持init/run/skill/mcp/sync命令

**Patterns to follow:**
- 参考 hermes-agent/cli-config.yaml.example 配置格式

**Test scenarios:**
- CLI命令 --help 正常显示
- config.yaml.example 可被正确解析
- 无外部依赖时项目可导入

**Verification:**
- `python -m ascend_op_agent --help` 成功
- `python -m pytest tests/test_config.py -v` 通过

---

- [ ] **Unit 2: Agent核心引擎**

**Goal:** 实现会话管理、7层Prompt组装、工具注册

**Requirements:** R1, KD1, KD3

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/agent/__init__.py`
- Create: `src/ascend_op_agent/agent/core.py`
- Create: `src/ascend_op_agent/agent/prompt_builder.py`
- Create: `src/ascend_op_agent/agent/tool_registry.py`
- Create: `src/ascend_op_agent/agent/context.py`
- Create: `tests/test_agent.py`

**Approach:**

*核心类 AIAgent:*
```python
class AIAgent:
    def __init__(self, config: AgentConfig):
        self.tool_registry = ToolRegistry()
        self.prompt_builder = PromptBuilder()
        self.context = ContextEngine()
        self.workflow = OperatorWorkflow(...)

    def run_conversation(self, user_input: str) -> str:
        # 7层Prompt组装 → LLM调用 → 工具执行 → 响应返回
```

*7层Prompt Assembly:*
1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory
6. Skills Index
7. Context Files + Timestamp + Env

*ToolRegistry自注册:*
```python
def register(name, toolset, schema, handler, check_fn=None):
    # 工具声明式注册，支持装饰器
```

**Patterns to follow:**
- hermes-agent/run_agent.py 的 AIAgent 类结构
- hermes-agent/tools/registry.py 的自注册机制

**Test scenarios:**
- AIAgent初始化成功
- ToolRegistry可正确注册和发现工具
- PromptBuilder生成正确的分层Prompt

**Verification:**
- `python -m pytest tests/test_agent.py -v` 通过
- Agent可处理简单对话并调用工具

---

- [ ] **Unit 3: SSH开发模式**

**Goal:** 实现本地和远程开发模式，支持SSH连接和文件同步

**Requirements:** R1.3, KD2, Deferred-SSH

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/ssh/__init__.py`
- Create: `src/ascend_op_agent/ssh/manager.py`
- Create: `src/ascend_op_agent/ssh/sync.py`
- Create: `tests/test_ssh.py`

**Approach:**

*SSHManager:*
```python
class SSHManager:
    def connect(self, host, user, key_path, password=None):
        # paramiko SSHClient，指数退避重连

    def exec_command(self, cmd: str) -> CommandResult:
        # 远程执行命令

    def sync_files(self, local_path, remote_path, direction):
        # rsync增量同步
```

*文件同步策略:*
- 使用rsync --checksum进行增量同步
- 同步前校验文件hash
- 冲突处理：覆盖或备份

**Patterns to follow:**
- 参考agent-skills/ascendc-operator-dev的远程开发模式

**Test scenarios:**
- SSH连接失败时指数退避重连
- rsync增量同步正确
- 本地/远程模式可切换

**Verification:**
- `python -m pytest tests/test_ssh.py -v` 通过

---

- [ ] **Unit 4: MCP服务器集成**

**Goal:** 实现MCP客户端，支持stdio和HTTP两种连接模式

**Requirements:** R8, KD6

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/mcp/__init__.py`
- Create: `src/ascend_op_agent/mcp/client.py`
- Create: `src/ascend_op_agent/mcp/server_config.py`
- Create: `tests/test_mcp.py`

**Approach:**

*MCPClient:*
```python
class MCPClient:
    def __init__(self, config: MCPConfig):
        self.transport = StdioTransport() if config.type == 'stdio' else HTTPTransport()

    def connect(self):
        # 启动MCP服务器或连接HTTP端点

    def call_tool(self, tool_name, args):
        # 调用MCP工具

    def list_tools(self):
        # 获取可用工具列表
```

*配置解析:*
```yaml
mcp:
  servers:
    - name: code-search
      type: stdio
      command: npx /path/to/server
    - name: knowledge-retrieval
      type: http
      url: http://localhost:8080
      auth: bearer  # optional
```

**Patterns to follow:**
- mcp-sdk Python客户端模式
- hermes-agent的MCP集成方式

**Test scenarios:**
- stdio模式MCP服务器启动
- HTTP模式MCP服务器连接
- 连接失败时记录警告不阻塞

**Verification:**
- `python -m pytest tests/test_mcp.py -v` 通过

---

- [ ] **Unit 5: Skill仓库管理**

**Goal:** 实现Skill仓库同步和本地索引，支持git仓库和本地路径

**Requirements:** R9, KD7, KD8, Deferred-Skill

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/skills/__init__.py`
- Create: `src/ascend_op_agent/skills/repository.py`
- Create: `src/ascend_op_agent/skills/index.py`
- Create: `src/ascend_op_agent/skills/storage.py`
- Create: `tests/test_skills.py`

**Approach:**

*SkillRepository:*
```python
class SkillRepository:
    def __init__(self, config: SkillsConfig):
        self.cache_dir = config.cache_dir
        self.index = SkillIndex()  # SQLite FTS5

    def sync(self, repo_name: str = None):
        # git clone/pull 或 cp -r 本地路径
        # 更新索引

    def search(self, query: str) -> List[Skill]:
        # FTS5全文检索
```

*混合组织结构:*
```
~/.ascend_op_agent/skills/
├── ascendc_elementwise/
│   ├── SKILL.md
│   ├── templates/
│   └── references/
├── ascendc_elementwise_bugfix/
│   └── SKILL.md
└── ascendc_elementwise_performance/
    └── SKILL.md
```

**Patterns to follow:**
- SKILL.md格式（Hermes Agent）
- 两层缓存（内存+磁盘）

**Test scenarios:**
- git仓库克隆成功
- 本地路径Skill加载成功
- FTS5检索返回正确结果
- 多仓库冲突处理（按优先级）

**Verification:**
- `python -m pytest tests/test_skills.py -v` 通过

---

- [ ] **Unit 6: 工作流引擎（Phase 0-4）**

**Goal:** 实现六阶段工作流的前四阶段（初始化、需求分析、方案设计、代码生成、编译验证）

**Requirements:** R1, R2, R3, R4, R5, KD3

**Dependencies:** Unit 2, Unit 3

**Files:**
- Create: `src/ascend_op_agent/workflow/__init__.py`
- Create: `src/ascend_op_agent/workflow/engine.py`
- Create: `src/ascend_op_agent/workflow/phases.py`
- Create: `src/ascend_op_agent/workflow/compiler.py`
- Create: `tests/test_workflow.py`

**Approach:**

*OperatorWorkflow:*
```python
class OperatorWorkflow:
    def __init__(self, agent: AIAgent, ssh: SSHManager):
        self.phases = [
            Phase0Init(),
            Phase1Analysis(),
            Phase2Design(),
            Phase3CodeGen(),
            Phase4Verify(),
        ]

    def run(self, user_input: str, context: dict):
        for phase in self.phases:
            result = phase.execute(context)
            if phase.requires_confirmation():
                yield result  # 等待用户确认
            context.update(result)
```

*Phase0 初始化:*
- 解析用户输入（算子名、描述、Shape/Dtype）
- 检测参考代码（CUDA/CUTLASS/Triton）
- 环境初始化（本地/远程）

*Phase1 需求分析:*
- 算子类型识别
- 复杂度评估
- 生成分析报告

*Phase2 方案设计:*
- 内存布局选择
- Tiling策略
- GPU迁移场景的架构映射

*Phase3 代码生成:*
- AscendC代码生成
- 测试代码生成
- CMakeLists.txt生成

*Phase4 编译验证:*
- 自动编译（最多3次修复）
- 确定性修复：语法/拼写/缺失头文件/类型不匹配
- 单测执行

**Patterns to follow:**
- ascendc-operator-dev skill 的六阶段工作流

**Test scenarios:**
- Phase0正确解析用户输入
- Phase1生成分析报告
- Phase2等待用户确认
- Phase4编译失败时自动修复（确定性错误）

**Verification:**
- `python -m pytest tests/test_workflow.py -v` 通过
- 端到端测试：用户输入 → 方案确认 → 代码生成 → 编译通过

---

- [ ] **Unit 7: 框架适配（Phase 5）**

**Goal:** 实现PyTorch/TensorFlow适配器生成

**Requirements:** R6, KD5

**Dependencies:** Unit 6

**Files:**
- Create: `src/ascend_op_agent/workflow/adapters.py`
- Create: `tests/test_adapters.py`

**Approach:**

*FrameworkAdapter:*
```python
class PyTorchAdapter:
    def generate(self, op_info: OpInfo) -> List[FileChange]:
        # 生成 torch_adapter.cpp
        # 生成 torch_test.py

class TensorFlowAdapter:
    def generate(self, op_info: OpInfo) -> List[FileChange]:
        # 生成 tf_operator.cc
        # 生成 tf_test.py
```

**Patterns to follow:**
- ascendc-operator-dev 的 framework adapter 模式

**Test scenarios:**
- PyTorch适配代码生成
- TensorFlow适配代码生成
- 版本兼容性检测

**Verification:**
- `python -m pytest tests/test_adapters.py -v` 通过

---

- [ ] **Unit 8: 技能保存（Phase 6）**

**Goal:** 实现技能保存和发布流程

**Requirements:** R7, KD4, KD8, KD9, KD10

**Dependencies:** Unit 5, Unit 6

**Files:**
- Create: `src/ascend_op_agent/workflow/skill_save.py`
- Create: `tests/test_skill_save.py`

**Approach:**

*SkillSaver:*
```python
class SkillSaver:
    def save(self, op_result: OpResult, user_confirm: bool):
        if not user_confirm:
            return

        # 提取经验
        template = self.extract_template(op_result)
        bugfix = self.extract_bugfix(op_result)
        perf = self.extract_performance(op_result)

        # 保存到混合维度结构
        self.repo.save(f"{backend}_{op_name}/", template)
        self.repo.save(f"{backend}_{op_name}_bugfix/", bugfix)
        self.repo.save(f"{backend}_{op_name}_performance/", perf)
```

*发布流程:*
```bash
skill publish <skill_name>  # → git push → PR创建
```

**Patterns to follow:**
- KD8混合组织结构
- KD10 PR审核流程

**Test scenarios:**
- 技能正确保存到三种维度
- skill publish生成PR

**Verification:**
- `python -m pytest tests/test_skill_save.py -v` 通过

---

- [ ] **Unit 9: 集成测试**

**Goal:** 端到端集成测试，验证完整工作流

**Requirements:** All

**Dependencies:** Units 1-8

**Files:**
- Create: `tests/integration/`
- Create: `tests/integration/test_e2e_local.py`
- Create: `tests/integration/test_e2e_remote.py`
- Create: `tests/integration/test_mcp_integration.py`
- Create: `tests/integration/test_skill_flow.py`

**Approach:**
- 使用mock模拟SSH和远程环境
- 使用stub模拟LLM调用
- 测试完整用户旅程

**Test scenarios:**
- 本地模式完整工作流
- 远程模式完整工作流
- MCP工具调用
- 技能保存和检索

**Verification:**
- `python -m pytest tests/integration/ -v` 通过

## System-Wide Impact

### Interaction Graph

| 组件 | 影响 |
|------|------|
| CLI | 新增命令（init/run/skill/mcp/sync） |
| Agent Core | 影响所有工作流阶段 |
| SSH Manager | 影响Phase0和Phase4 |
| MCP Client | 影响工具调用 |
| Skill Repository | 影响Phase1和Phase6 |

### Error Propagation

- SSH失败 → 降级到本地模式或报告用户
- MCP连接失败 → 记录警告，继续执行
- 编译失败 → 自动修复3次，失败则报告
- LLM调用失败 → 指数退避重试

## Risks & Dependencies

| 风险 | 影响 | 缓解 |
|------|------|------|
| SSH重连策略不完善 | 远程开发体验 | 单元测试覆盖 |
| MCP服务器不稳定 | 工具调用失败 | 容错设计，不阻塞主流程 |
| LLM幻觉导致错误代码 | 编译失败 | 确定性修复范围限定 |
| Skill检索不准确 | 经验复用效果差 | 迭代改进检索算法 |

## Phased Delivery

### Phase 1: 核心框架（Unit 1-2）
- 项目脚手架
- Agent核心引擎
- **目标**: 本地模式基本对话

### Phase 2: 开发模式（Unit 3）
- SSH管理器
- 文件同步
- **目标**: 远程开发可用

### Phase 3: 工作流（Unit 6）
- 六阶段工作流
- **目标**: 完整算子开发流程

### Phase 4: 集成能力（Unit 4-5, 7-8）
- MCP集成
- Skill仓库
- 框架适配
- 技能保存
- **目标**: 完整功能可用

### Phase 5: 测试和优化（Unit 9）
- 集成测试
- 端到端验证
- **目标**: 生产可用

## Documentation Plan

| 文档 | 内容 |
|------|------|
| README.md | 安装、快速开始 |
| docs/architecture.md | 系统架构设计 |
| docs/workflow.md | 工作流说明 |
| docs/config.md | 配置参考 |
| docs/skill_format.md | Skill格式说明 |

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md](../brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md)
- Hermes Agent架构: [hermes-agent/.zread/wiki/](file:///Users/huangshilei/Documents/pythonprojects/hermes-agent/.zread/wiki/)
- ascendc-operator-dev skill: `/tmp/agent-skills/skills/ascendc-operator-dev/SKILL.md`
- MCP SDK: https://github.com/modelcontextprotocol/python-sdk
