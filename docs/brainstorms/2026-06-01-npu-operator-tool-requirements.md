# NPU 算子开发工具增强方案

## 1. 背景

当前 Agent 仅支持 3 个基础工具（`file_read`, `file_write`, `shell_exec`），无法满足昇腾 NPU 算子开发全流程需求。

本方案参考 Hermes Agent 的工具架构，设计一套可扩展的工具系统。

---

## 2. 架构设计

### 2.1 核心组件

```
tool_registry.py       # 工具注册中心（单例）
tool_entry.py          # 工具元数据结构
toolset.py             # 工具集分组管理
tools/                 # 各工具实现模块
  ├── __init__.py     # 扫描注册
  ├── file_read_tool.py
  ├── file_write_tool.py
  ├── file_search_tool.py
  ├── patch_tool.py
  ├── shell_tool.py
  ├── python_exec_tool.py
  └── git_tool.py
```

### 2.2 ToolEntry 数据结构

```python
@dataclass
class ToolEntry:
    name: str                    # 工具名
    toolset: str                 # 所属工具集
    schema: dict                 # OpenAI function calling schema
    handler: Callable           # 处理函数
    check_fn: Callable = None    # 可用性检查函数
    requires_env: list = None    # 所需环境变量
    is_async: bool = False       # 是否异步
    emoji: str = ""
    description: str = ""
    max_result_size: int = 100_000
```

### 2.3 自注册机制

每个工具模块在 import 时执行 `registry.register()`，Registry 使用 AST 扫描 + `importlib` 动态加载发现所有工具。

### 2.4 工具集分组

```python
TOOLSETS = {
    "file": {
        "description": "文件操作工具",
        "tools": ["file_read", "file_write", "file_search", "patch"],
        "includes": [],
    },
    "terminal": {
        "description": "终端执行工具",
        "tools": ["shell_exec"],
        "includes": [],
    },
    "python": {
        "description": "Python 执行工具",
        "tools": ["python_exec"],
        "includes": [],
    },
    "git": {
        "description": "Git 版本控制工具",
        "tools": ["git_log", "git_diff", "git_status", "git_branch"],
        "includes": [],
    },
    "npu": {
        "description": "NPU 开发工具",
        "tools": ["npu_smi", "msop", "cann_compile"],
        "includes": [],
    },
}
```

---

## 3. 工具实现清单

### 3.1 复用 Hermes 实现（P0）

| 工具 | 来源 | 说明 |
|------|------|------|
| `file_read` | Hermes `file_tools.py` | 带 dedup、loop 检测、大文件保护 |
| `file_write` | Hermes `file_tools.py` | 原子写入、目录创建 |
| `file_search` | Hermes `file_tools.py` | grep 搜索、文件名搜索 |
| `patch` | Hermes `file_tools.py` | replace 模式和 unified diff |
| `shell_exec` | Hermes `terminal_tool.py` | 多环境支持（local/SSH/Docker） |
| `python_exec` | Hermes `code_execution_tool.py` | 安全执行 Python 代码 |

### 3.2 新设计实现（P1）

| 工具 | 功能 | 优先级 |
|------|------|--------|
| `git_log` | 查看提交历史 | P0 |
| `git_diff` | 查看文件变更 | P0 |
| `git_status` | 查看仓库状态 | P0 |
| `git_branch` | 分支管理 | P1 |
| `npu_smi` | NPU 设备信息 | P1 |
| `msop` | 算子分析工具 | P1 |
| `cann_compile` | CANN 编译包装 | P2 |

### 3.3 Shell 工具多环境架构

```
BaseEnvironment (抽象基类)
├── LocalEnvironment      # 本地执行
├── SSHEnvironment        # SSH 远程执行
├── DockerEnvironment     # 容器化执行
└── NPUEnvironment        # NPU 设备执行 (新增)
```

---

## 4. 注册流程

```
工具模块加载时:
  ↓
registry.register(
    name="file_read",
    toolset="file",
    schema=FILE_READ_SCHEMA,
    handler=_handle_file_read,
    check_fn=_check_env,
    emoji="📖",
)
  ↓
ToolEntry 存入 _tools dict
  ↓
tool_registry.list_tools() 可获取所有工具
  ↓
to_openai_format() 生成 LLM 调用 schema
```

---

## 5. 优先级计划

| 阶段 | 工具 | 工作内容 |
|------|------|----------|
| **Phase 1** | `file_read`, `file_write`, `shell_exec` | 迁移 Hermes 实现，保留现有接口 |
| **Phase 2** | `file_search`, `patch` | 迁移 Hermes 搜索和 patch 能力 |
| **Phase 3** | `python_exec` | Python 代码执行 |
| **Phase 4** | `git_*` 系列 | Git 工具集 |
| **Phase 5** | `npu_*` 系列 | NPU 硬件工具 |

---

## 6. 关键设计决策

1. **向后兼容**: 保留现有 `tool_registry.register()` API
2. **多环境支持**: shell_exec 支持 SSH 远程执行（参考 Hermes）
3. **安全优先**: Python 执行使用子进程隔离，超时控制
4. **性能优化**: 文件读取结果缓存，重复检测
5. **可测试性**: 每个工具独立测试，check_fn 可 mock

---

## 7. 依赖假设

- 工具模块放在 `src/ascend_op_agent/agent/tools/` 目录
- 需要新增 `npu` 工具集支持 CANN 工具链
- SSH 环境需要配置远程服务器信息

---

## 8. 待验证假设

- [ ] Hermes 的 file_tools 是否可以直接复用（LICENSE 兼容）
- [ ] Python 执行的安全边界如何定义
- [ ] NPU 环境的远程连接方式（SSH vs CANN SDK）