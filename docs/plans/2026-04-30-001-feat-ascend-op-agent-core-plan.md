---
title: "feat: Ascend Op Agent Core System"
type: feat
status: active
date: 2026-04-30
origin: "docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md"
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

## 用户价值指标（量化）

| 指标 | 当前状态 | 目标（使用Agent后） | 衡量方式 |
|------|----------|---------------------|----------|
| 开发周期 | 手动5-10天/算子 | 1-2天/算子 | 项目统计 |
| 代码复用率 | ~20% | >60% | Skill使用次数 |
| 编译通过率（首次） | ~40% | >75% | Phase4统计 |
| 迁移效率（GPU→Ascend） | 手动2-3周 | 3-5天 | 项目统计 |
| 累计经验损失 | 高（人员流动） | 低（Skill持久化） | 知识库规模 |

## Success Criteria

## Requirements Trace

| ID | 需求 | 来源 |
|----|------|------|
| R1 | 会话初始化（本地/远程模式） | KD2 |
| R2 | 需求分析阶段（自动） | KD1 |
| R3 | 方案设计阶段（用户确认） | KD1 |
| R4 | 代码生成阶段 | KD3 |
| R5 | 编译验证（自动修复最多3次） | KD3 |
| R5.1 | 精度评估（≥30用例，必选） | KD8 |
| R6 | 框架适配（可选） | KD5 |
| R7 | 技能保存（可选） | KD4 |
| R8 | MCP服务器集成 | KD6 |
| R9 | Skill仓库管理 | KD7 |
| R10 | 性能评测报告（必选） | KD8 |

## Scope Boundaries

### In Scope (MVP)
- Agent核心引擎（会话管理、Prompt组装、工具注册）
- 本地开发模式
- 远程开发模式（SSH）
- 六阶段工作流（R1-R5）
- Phase5 精度评估（≥30用例，必选）
- Phase8 性能评测报告（必选）

### In Scope (Future)
- R6 框架适配（PyTorch/TensorFlow）
- R7 技能保存与发布
- R8 MCP服务器集成
- R9 Skill仓库管理

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

### KD-6: MCP服务器集成
**决策**: 支持通过配置文件连接外部MCP服务器
**理由**: 复用社区已有的MCP工具和服务

### KD-7: Skill仓库混合组织
**决策**: 技能按混合维度组织——基础模板 + Bugfix + 性能优化
**理由**: 不同经验有不同复用场景

### KD-8: 性能评测为必选阶段
**决策**: Phase 7性能评测使用torch_npu.profiler对比基准
**理由**: 确保算子性能达标

### KD-9: LLM API通过环境变量配置
**决策**: API密钥通过环境变量获取，不硬编码
**理由**: 安全性要求

### KD-10: 敏感信息统一管理
**决策**: 密码/token通过系统密钥链（keyring库）存储，环境变量中仅存引用
**理由**: 防止凭据泄露

### KD-11: SSH密码凭据保护
**决策**: SSH密码不使用明文存储；支持keyring或提示用户每次输入
**理由**: KD-10的具体化

### KD-12: MCP认证token安全获取
**决策**: HTTP bearer token从环境变量或keyring获取，不在配置文件明文
**理由**: KD-10的具体化

### KD-13: 参考 Hermes Agent 架构独立实现
**决策**: 完全独立实现核心模块，仅参考 Hermes Agent 的设计
**理由**: 不引入外部依赖，保持项目独立性

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
│    ├── Phase5: 精度评估（≥30用例，必选）                     │
│    ├── Phase6: 框架适配（可选）                              │
│    ├── Phase7: 技能保存（可选）                             │
│    └── Phase8: 性能评测报告（必选）                         │
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
├─────────────────────────────────────────────────────────────┤
│  安全层 (内置)                                               │
│    ├── CredentialManager: keyring集成，密码/token安全存储      │
│    ├── SSHClaimStorage: SSH凭据管理，不明文存储                │
│    └── TokenResolver: 环境变量/keyring获取MCP bearer token   │
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
- pyproject.toml: click, rich, pydantic, pyyaml, sqlparse, fts5, paramiko, mcp
- config.yaml.example: 包含所有配置项及注释
- CLI使用click + rich构建，支持init/run/skill/mcp/sync命令
- 独立实现核心模块，参考 Hermes Agent 设计模式

**Patterns to follow:**
- 参考 Hermes Agent 的 cli-config.yaml.example 配置格式（独立实现）

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
- Create: `src/ascend_op_agent/agent/memory.py`        # Layer 5 Persistent Memory
- Create: `src/ascend_op_agent/agent/SOUL.md`  # Agent Identity定义
- Create: `tests/test_agent.py`
- 独立实现：参考 Hermes Agent 的 AIAgent、ToolRegistry、PromptBuilder 架构

**Approach:**

*核心类 AIAgent:*
```python
class AIAgent:
    def __init__(self, config: AgentConfig):
        self.tool_registry = ToolRegistry()
        self.prompt_builder = PromptBuilder()
        self.context = ContextEngine()
        self.memory = PersistentMemory()  # Layer 5: 持久化记忆
        self.workflow = OperatorWorkflow(...)

    def run_conversation(self, user_input: str) -> str:
        # 7层Prompt组装 → LLM调用 → 工具执行 → 响应返回
```

*7层Prompt Assembly:*
1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory (SQLite持久化)
6. Skills Index
7. Context Files + Timestamp + Env

*ContextEngine 实现细节 (参考 Hermes 优先级互斥模式):*
```python
class ContextEngine:
    """参考 Hermes build_context_files_prompt - 优先级互斥加载"""
    PRIORITY_FILES = ['.hermes.md', 'AGENTS.md', 'CLAUDE.md', '.cursorrules']

    def __init__(self, max_tokens: int = 128000):
        self.max_tokens = max_tokens
        self._context_cache = {}  # 缓存已加载的上下文

    def build_context_prompt(self, workspace_path: str) -> str:
        """只加载一个最高优先级文件（互斥模式）"""
        for filename in self.PRIORITY_FILES:
            filepath = os.path.join(workspace_path, filename)
            if os.path.exists(filepath):
                return self._load_and_scan(filepath)
        return ""

    def _load_and_scan(self, filepath: str) -> str:
        """加载文件内容并做安全扫描（防止注入）"""
        if filepath in self._context_cache:
            return self._context_cache[filepath]
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        # Hermes 的 _scan_context_content() 防止注入
        content = self._sanitize(content)
        self._context_cache[filepath] = content
        return content

    def _sanitize(self, content: str) -> str:
        """防止提示词注入 - 参考 Hermes 安全扫描"""
        # 检测威胁模式 + 不可见 Unicode 字符
        threat_patterns = [
            r'\x00', r'\u200b', r'\u202b', r'\ufeff',  # 不可见字符
            r'[\梯队]', r'软体',  # 中文威胁词
        ]
        for pattern in threat_patterns:
            content = re.sub(pattern, '', content)
        return content

    def retrieve(self, query: str, k: int = 5) -> list[Chunk]:
        # 向量检索 + FTS5 混合检索（用于 Skills 搜索）
        pass
```

*PersistentMemory 实现细节 (参考 Hermes MemoryStore):*
```python
class MemoryStore:
    """参考 Hermes MemoryStore - 支持多 pool 和快照冻结"""
    def __init__(self):
        self._memory_pools = {
            "memory": [],  # Agent 记忆
            "user": [],    # 用户偏好
        }
        self._snapshot = None  # 会话级冻结快照

    def add(self, pool: str, content: str):
        self._memory_pools[pool].append(content)
        self._invalidate_snapshot()

    def format_for_system_prompt(self, pool: str) -> str:
        """返回格式化后的记忆内容，用于 Layer 5"""
        if not self._memory_pools[pool]:
            return ""
        return f"[{pool.upper()}]:\n" + "\n".join(self._memory_pools[pool])

    def freeze_snapshot(self):
        """缓存一致性：会话级冻结，避免写入破坏缓存"""
        self._snapshot = {k: list(v) for k, v in self._memory_pools.items()}

    def restore_snapshot(self):
        """恢复冻结的快照"""
        if self._snapshot:
            self._memory_pools = {k: list(v) for k, v in self._snapshot.items()}
```

*LLM重试策略:*
```python
class LLMClient:
    def __init__(self, config: LLMConfig):
        self.max_retries = 3
        self.backoff_factor = 2  # 指数退避
        self.fallback_models = ["gpt-4", "gpt-3.5-turbo"]

    def call(self, prompt: str, model: str = None) -> str:
        for attempt in range(self.max_retries):
            try:
                return self._do_call(prompt, model)
            except RateLimitError as e:
                wait_time = self.backoff_factor ** attempt
                time.sleep(wait_time)
            except ServiceUnavailableError:
                # 切换到fallback模型
                model = self.fallback_models.pop(0)
        raise MaxRetriesExceeded()
```

**Patterns to follow:**
- 参考 Hermes Agent 的 AIAgent 类结构（7层 Prompt Assembly）
- 参考 Hermes Agent 的 ToolRegistry 自注册机制（AST 扫描）

**Test scenarios:**
- AIAgent初始化成功
- ToolRegistry可正确注册和发现工具
- PromptBuilder生成正确的分层Prompt

**Verification:**
- `python -m pytest tests/test_agent.py -v` 通过
- Agent可处理简单对话并调用工具

---

- [ ] **Unit 3: SSH开发模式 + 安全层**

**Goal:** 实现本地和远程开发模式，支持SSH连接和文件同步；实现凭据安全管理

**Requirements:** R1, KD2, KD10, KD11, KD12

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/ssh/__init__.py`
- Create: `src/ascend_op_agent/ssh/manager.py`
- Create: `src/ascend_op_agent/ssh/sync.py`
- Create: `src/ascend_op_agent/security/__init__.py`
- Create: `src/ascend_op_agent/security/credential_manager.py`  # keyring集成
- Create: `src/ascend_op_agent/security/token_resolver.py`     # 环境变量/keyring
- Create: `tests/test_ssh.py`
- Create: `tests/test_security.py`

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

*敏感信息管理:*
```python
class CredentialManager:
    def get_ssh_password(self, host):
        # 从keyring获取，不存在则提示用户输入
        return keyring.get_password(f"ascend_op_agent:ssh:{host}", username)

    def set_ssh_password(self, host, username, password):
        # 存储到系统keyring
        keyring.set_password(f"ascend_op_agent:ssh:{host}", username, password)

class TokenResolver:
    def get_bearer_token(self, server_name):
        # 优先从环境变量获取，其次keyring
        token = os.getenv(f"OAUTH_TOKEN_{server_name.upper()}")
        if not token:
            token = keyring.get_password("ascend_op_agent:mcp", server_name)
        return token
```

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

**Goal:** 实现MCP客户端，支持stdio和HTTP两种连接模式；实现服务器生命周期管理

**Requirements:** R8, KD6, KD12

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/mcp/__init__.py`
- Create: `src/ascend_op_agent/mcp/client.py`
- Create: `src/ascend_op_agent/mcp/server_config.py`
- Create: `src/ascend_op_agent/mcp/lifecycle.py`    # 服务器启动/停止/健康检查
- Create: `tests/test_mcp.py`

**Approach:**

*MCP生命周期管理:*
```python
class MCPLifecycleManager:
    def __init__(self):
        self.processes: dict[str, subprocess.Popen] = {}
        self.health_checks: dict[str, float] = {}

    def start_server(self, config: MCPConfig) -> bool:
        if config.type == 'stdio':
            proc = subprocess.Popen(
                config.command.split(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self.processes[config.name] = proc
        # 启动后做健康检查

    def stop_server(self, name: str):
        if name in self.processes:
            self.processes[name].terminate()
            self.processes[name].wait(timeout=5)
            del self.processes[name]

    def health_check(self, name: str) -> bool:
        # 定期检查服务器是否响应
        # 超时则标记为不健康

    def cleanup(self):
        # 停止所有服务器
        for proc in self.processes.values():
            proc.terminate()
```

*MCPClient (参考 Hermes 支持三种传输模式):*
```python
class MCPClient:
    def __init__(self, config: MCPConfig, lifecycle: MCPLifecycleManager):
        self.lifecycle = lifecycle
        self.config = config
        # 支持三种传输模式（参考 Hermes）
        if config.type == 'stdio':
            self.transport = StdioTransport()
        elif config.type == 'http':
            self.transport = HTTPTransport()
        elif config.type == 'streamable-http':
            self.transport = StreamableHTTPTransport()  # Hermes 支持
        else:
            raise ValueError(f"Unsupported MCP transport type: {config.type}")

    def connect(self):
        # 获取 bearer token（支持 OAuth）
        token = self.oauth_manager.get_auth_header()
        if token:
            self.transport.set_auth_header(token)
        # 启动或连接 MCP 服务器
        self.lifecycle.start_server(self.config)

*MCPOAuthManager (参考 Hermes OAuth 流程):*
```python
class MCPOAuthManager:
    """参考 Hermes MCPOAuthManager - 支持 OAuth 2.0 客户端凭证流"""
    def __init__(self, server_config: MCPConfig):
        self.server_name = server_config.name
        self.oauth_config = server_config.get('oauth', {})

    def get_auth_header(self) -> Optional[str]:
        """获取认证头，支持 OAuth bearer token"""
        if self.oauth_config.get('type') == 'oauth':
            # 处理 OAuth 流程
            token = self._get_oauth_token()
            return f"Bearer {token}"
        # 回退到环境变量
        token = os.getenv(f"OAUTH_TOKEN_{self.server_name.upper()}")
        if token:
            return f"Bearer {token}"
        return None

    def _get_oauth_token(self) -> str:
        """OAuth 2.0 客户端凭证流"""
        # 实现 OAuth 2.0 客户端凭证流
        pass
```

*配置解析:*
```yaml
mcp:
  servers:
    - name: code-search
      type: stdio
      command: npx /path/to/server
    - name: knowledge-retrieval
      type: streamable-http  # 支持 streamable-http（参考 Hermes）
      url: http://localhost:8080
      oauth:
        type: oauth
        client_id: "..."
        client_secret: "..."
        token_url: "https://auth.example.com/oauth/token"
    - name: github
      type: http
      url: https://api.github.com/mcp
      headers:
        Authorization: "Bearer ${GITHUB_TOKEN}"  # 环境变量引用
```

**Patterns to follow:**
- mcp-sdk Python 客户端模式
- 参考 Hermes Agent 的 MCP 实现（含 StreamableHTTP + OAuth）
- 参考 Hermes `_mcp_loop` daemon 线程模式

**Test scenarios:**
- stdio 模式 MCP 服务器启动
- streamable-http 模式 MCP 服务器连接
- HTTP 模式 MCP 服务器连接（含 OAuth）
- 连接失败时记录警告不阻塞

**Verification:**
- `python -m pytest tests/test_mcp.py -v` 通过

---

- [ ] **Unit 5: Skill仓库管理**

**Goal:** 实现Skill仓库同步和本地索引，支持git仓库和本地路径

**Requirements:** R9, KD7

**Dependencies:** Unit 1

**Files:**
- Create: `src/ascend_op_agent/skills/__init__.py`
- Create: `src/ascend_op_agent/skills/repository.py`
- Create: `src/ascend_op_agent/skills/index.py`
- Create: `src/ascend_op_agent/skills/storage.py`
- Create: `tests/test_skills.py`

**Approach:**

*Skill检索算法 (参考 Hermes 两层缓存):*
```python
from functools import lru_cache
import json
import os

class SkillIndex:
    """参考 Hermes 两层缓存: LRU(进程内) + 磁盘快照"""
    MAX_LRU_CACHE = 8  # Hermes 最大 8 条
    SNAPSHOT_FILENAME = ".skills_prompt_snapshot.json"

    def __init__(self, db_path: str, cache_dir: str = None):
        self.conn = sqlite3.connect(db_path)
        self.cache_dir = cache_dir or os.path.expanduser("~/.ascend_op_agent")
        self.conn.execute("""
            CREATE VIRTUAL TABLE skills USING fts5(
                name, description, tags, content
            )
        """)
        self._load_snapshot()

    @lru_cache(maxsize=MAX_LRU_CACHE)
    def search_cached(self, query: str, k: int = 5) -> list[Skill]:
        """LRU 缓存（进程内）"""
        return self._do_search(query, k)

    def _do_search(self, query: str, k: int = 5) -> list[Skill]:
        """实际搜索逻辑"""
        results = self.conn.execute("""
            SELECT name, description, tags,
                   bm25(skills) as score
            FROM skills
            WHERE skills MATCH ?
            ORDER BY score
            LIMIT ?
        """, (query, k)).fetchall()
        return [Skill(name=r[0], description=r[1], tags=r[2].split(','))
                for r in results]

    def _load_skill_snapshot(self) -> dict:
        """加载磁盘快照（Hermes 快照机制）"""
        snapshot_path = os.path.join(self.cache_dir, self.SNAPSHOT_FILENAME)
        if os.path.exists(snapshot_path):
            with open(snapshot_path, 'r') as f:
                return json.load(f)
        return {}

    def _save_skill_snapshot(self):
        """保存磁盘快照"""
        snapshot_path = os.path.join(self.cache_dir, self.SNAPSHOT_FILENAME)
        os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
        # 保存 LRU 缓存内容到磁盘
        snapshot = {
            "lru_cache_keys": list(self.search_cached.cache_info().keys),
            "timestamp": time.time()
        }
        with open(snapshot_path, 'w') as f:
            json.dump(snapshot, f)

    def add_skill(self, skill: Skill):
        self.conn.execute(
            "INSERT INTO skills VALUES (?, ?, ?, ?)",
            (skill.name, skill.description, ','.join(skill.tags), skill.content)
        )
        # 清除 LRU 缓存并更新快照
        self.search_cached.cache_clear()
        self._save_skill_snapshot()
```

*混合组织结构:*

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

- [ ] **Unit 6: 工作流引擎（Phase 0-5）**

**Goal:** 实现六阶段工作流（初始化、需求分析、方案设计、代码生成、编译验证、精度评估）

**Requirements:** R1, R2, R3, R4, R5, R5.1, KD3

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
- 检测参考代码（CUDA/CUTLASS/Triton）→ 识别为GPU迁移场景
- 环境初始化（本地/远程）

*GPU迁移场景处理:*
```python
class OperatorWorkflow:
    def detect_migration_scenario(self, user_input: str) -> bool:
        # 检测是否有GPU参考代码
        return any(keyword in user_input.lower()
            for keyword in ['cuda', 'cutlass', 'triton', 'gpu', '参考'])

    def get_migration_strategy(self, ref_code_path: str) -> MigrationStrategy:
        if 'cuda' in ref_code_path or 'cutlass' in ref_code_path:
            return MigrationStrategy.CUDA_TO_ASCENDC
        elif 'triton' in ref_code_path:
            return MigrationStrategy.TRITON_TO_ASCENDC
        return MigrationStrategy.FROM_SCRATCH

    def generate_architecture_mapping(self, ref_code) -> dict:
        # CUDA/Triton → AscendC 架构映射
        # 共享内存 → Global Memory
        # Thread → Tiling
        # std::vector → Tensor
        return mapping
```

*Phase1 需求分析:*
- 算子类型识别
- 复杂度评估（GPU迁移复杂度评分）
- 生成分析报告（含迁移可行性）

*Phase2 方案设计:*
- 内存布局选择
- Tiling策略（继承GPU的tiling策略）
- GPU迁移场景的架构映射（自动生成）
- 迁移方案评审（用户确认）

*Phase3 代码生成:*
- AscendC代码生成（基于设计文档的KernelHost契约）
- CATLASS代码生成（基于catlass/examples模板）
- Triton代码生成（基于triton-kernel模板）
- 测试代码生成
- CMakeLists.txt生成

*AscendC代码生成策略:*
```python
class AscendCCodeGen:
    TEMPLATES = {
        'elementwise': 'templates/ascendc_elementwise_kernel.cpp',
        'matmul': 'templates/ascendc_matmul_kernel.cpp',
        'reduction': 'templates/ascendc_reduction_kernel.cpp',
    }

    def generate(self, op_info: OpInfo) -> FileChanges:
        # 1. 解析design.md获取算子规格
        # 2. 根据算子类型选择模板
        # 3. 填充模板变量（shape/dtype/tiling）
        template_path = self.TEMPLATES[op_info.op_type]
        return self.fill_template(template_path, op_info)
```

*CATLASS代码生成策略:*
```python
class CatlassCodeGen:
    def find_similar_example(self, design: DesignDoc) -> Example:
        # 基于算子类型+输入维度+dtype匹配最相似示例
        return self.examples.best_match(design.op_type, design.input_shapes)

    def generate(self, design: DesignDoc) -> FileChanges:
        # 1. 从catlass/examples找到最相似的示例
        # 2. 提取KernelBuilder/HostBuilder模式
        # 3. 按design.md规格调整参数
        example = self.find_similar_example(design)
        return self.adapt(example, design)
```

*GPU迁移架构映射:*
```python
ARCHITECTURE_MAPPING = {
    'cuda': {
        'shared_memory': 'Global Memory',
        'thread': 'Tiling',
        'std::vector': 'Tensor',
        'cudaMalloc': 'AllocTensor',
    },
    'triton': {
        'tl.load': 'LoadTensor',
        'tl.store': 'StoreTensor',
        'triton.jit': 'AscendC Kernel',
    }
}
```

*Phase4 编译验证:*
- 自动编译（最多3次修复）
- 确定性修复：语法/拼写/缺失头文件/类型不匹配
- 单测执行

*Phase5 精度评估（必选）:*
```python
class PrecisionEvaluator:
    def __init__(self, test_cases: int = 30):
        self.min_test_cases = 30  # 最少30例

    def evaluate(self, operator, design: DesignDoc) -> PrecisionReport:
        # 1. 生成≥30个测试用例（shapes × dtypes × 边界）
        # 2. 对比AscendC算子输出与numpy/参考实现
        # 3. 计算误差指标：abs_err, rel_err, cos_sim
        # 4. 生成精度报告
        return PrecisionReport(passed=passed_cases, failed=failed_cases,
                               report_path="test/precision_report.md")
```

*精度评估检查点:*
- [ ] 测试用例数 ≥ 30
- [ ] 覆盖多种shape（边界值、常规值）
- [ ] 覆盖多种dtype（float16, float32, int8, int32）
- [ ] 精度报告已生成（Markdown格式）
- [ ] 聊天界面展示精度结果摘要

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

- [ ] **Unit 7: 框架适配（Phase 6）**

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

- [ ] **Unit 8: 技能保存（Phase 7）**

**Goal:** 实现技能保存和发布流程

**Requirements:** R7, KD7

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

---

- [ ] **Unit 10: 性能评测报告（Phase 8）**

**Goal:** 实现性能评测和报告生成

**Requirements:** R10, KD8

**Dependencies:** Unit 6 (Phase 4编译验证通过)

**Files:**
- Create: `src/ascend_op_agent/workflow/performance.py`
- Create: `tests/test_performance.py`

**Approach:**

*PerformanceEvaluator:*
```python
class PerformanceEvaluator:
    def evaluate(self, operator, benchmark_cases):
        # 使用torch_npu.profiler采集性能数据
        # warmup=5, active=5
        # 生成性能对比报告
```

*性能评测流程:*
- 生成JSONL格式测试用例
- 使用torch_npu.profiler进行profiling
- 汇总op_statistic.csv指标
- 输出Markdown格式性能对比报告

**Patterns to follow:**
- ascendc-operator-performance-eval skill规范

**Test scenarios:**
- profiler数据采集成功
- 性能报告生成正确
- 自定义算子vs标杆对比显示

**Verification:**
- `python -m pytest tests/test_performance.py -v` 通过

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
- Agent核心引擎（含SOUL.md）
- **目标**: 本地模式基本对话

### Phase 2: 开发模式（Unit 3）
- SSH管理器
- 文件同步
- **目标**: 远程开发可用

### Phase 3: 工作流+性能（Unit 6, Unit 10）
- 六阶段工作流（含Phase 0-4）
- Phase 7性能评测
- **目标**: 完整算子开发流程+性能报告

### Phase 4: 可选功能（Unit 4-5, 7-8）
- MCP集成
- Skill仓库
- 框架适配
- 技能保存
- **目标**: 扩展功能可用

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
- **Hermes Agent 架构参考:** [hermes-agent/.zread/wiki/](file:///Users/huangshilei/Documents/pythonprojects/hermes-agent/.zread/wiki/) （仅参考，不引入）
- ascendc-operator-dev skill: 配置于 `~/.ascend_op_agent/agent-skills/` 或通过 `--skills-path` 指定
- MCP SDK: https://github.com/modelcontextprotocol/python-sdk
