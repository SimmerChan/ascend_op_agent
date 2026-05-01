---
title: "refactor: SSH远程执行环境重构"
type: refactor
status: active
date: 2026-05-01
origin: "docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md"
---

# SSH远程执行环境重构规划

## Overview

基于Hermes Agent设计思路，对Ascend Op Agent的SSH模块进行深度重构，解决会话管理缺失、连接效率低下、容器支持不完整等20+设计缺陷。

## Problem Frame

当前SSH模块存在以下核心问题：
1. **会话管理缺失** - 无会话快照、无CWD持久化，导致环境变量和目录无法跨命令保持
2. **连接效率低下** - 无ControlMaster复用，每次命令执行都有SSH握手开销
3. **容器支持不完整** - RemoteEnvValidator是空壳，无法真正管理容器生命周期
4. **安全性不足** - 过于宽松的主机密钥策略、无PTY支持
5. **架构扩展性差** - 硬编码SSH实现，无法透明切换到其他后端

## Requirements Trace

| ID | 需求 | 来源 |
|----|------|------|
| R1 | 会话快照机制 - 跨命令保持环境变量/函数/别名 | 用户体验需求 |
| R2 | CWD持久化 - 追踪远程工作目录 | Agent行为正确性 |
| R3 | ControlMaster连接复用 - 减少SSH握手开销 | 性能需求 |
| R4 | 后台进程处理 - 支持 `cmd &` 运行 | 功能完整性 |
| R5 | 命令中断支持 - 用户可终止长时运行命令 | 用户体验需求 |
| R6 | 文件同步优化 - 批量上传、断点续传 | 性能需求 |
| R7 | 容器生命周期管理 - 创建/启动/停止容器 | 未来扩展 |
| R8 | 抽象后端接口 - 支持Local/Docker/SSH多后端 | 架构扩展性 |

## Scope Boundaries

### In Scope
- SSH连接管理重构（ControlMaster、会话快照）
- CWD持久化机制
- 文件同步优化（批量上传、增量缓存）
- 后台进程和中断处理
- RemoteEnvValidator容器管理实现

### Out of Scope
- 多后端架构（Local/Docker/Singularity/Modal/Daytona） - 单独规划
- 安全强化（主机密钥策略） - 作为后续改进
- PTY支持 - 需要时再实现

## Key Technical Decisions

### KD-1: 复用 Hermes BaseEnvironment 架构
**决策**: 引入 `BaseEnvironment` 抽象基类，统一所有后端接口
**理由**: Hermes已验证的设计，支持透明切换后端；调用方无需感知具体实现

### KD-2: 引入会话快照机制
**决策**: 每个环境初始化时执行 `init_session()` 捕获shell状态，存入快照文件
**理由**: 解决环境变量跨命令保持问题；比每次执行login shell更高效

### KD-3: CWD通过stdout标记追踪
**决策**: 使用 `__HERMES_CWD_<session_id>__` 标记追踪工作目录
**理由**: 远程后端可解析stdout获取当前目录；本地后端使用临时文件

### KD-4: SSH使用ControlMaster复用连接
**决策**: 通过ControlPath复用SSH连接，避免每次握手的性能开销
**理由**: Hermes验证的方案；大幅降低多命令场景的延迟

### KD-5: FileSyncManager与后端解耦
**决策**: FileSyncManager通过函数式接口（upload_fn/delete_fn/bulk_upload_fn）工作
**理由**: 后端可独立替换；支持未来多后端扩展

### KD-6: _ThreadedProcessHandle封装非subprocess后端
**决策**: SDK后端（Modal/Daytona）通过 `_ThreadedProcessHandle` 适配
**理由**: 统一ProcessHandle接口；所有后端对上层透明

### KD-7: RemoteEnvValidator实现容器管理
**决策**: 实现docker命令执行；支持容器创建/启动/停止/删除
**理由**: 当前RemoteEnvValidator是空壳，无法满足容器化开发场景

## Open Questions

### Resolved During Planning

| 问题 | 解决方案 |
|------|----------|
| 会话快照存储位置 | `/tmp/hermes-snap-<session_id>.sh` |
| CWD标记格式 | `__HERMES_CWD_<session_id>__<path>__HERMES_CWD_<session_id>__` |
| ControlSocket路径 | `{temp_dir}/hermes-ssh/<hash>.sock` （避免macOS 104字节限制）|
| 批量文件传输 | tar-over-SSH管道，避免逐文件scp |

### Deferred to Implementation

- 具体的环境变量过滤策略（参考Hermes的blocklist）
- stdin heredoc模式的具体实现细节
- 中断信号处理的具体实现

## High-Level Technical Design

```
┌─────────────────────────────────────────────────────────────┐
│                    BaseEnvironment (ABC)                     │
├─────────────────────────────────────────────────────────────┤
│ + execute(command, cwd, timeout, stdin_data) -> dict         │
│ + init_session()                                            │
│ + cleanup()                                                 │
│ + _run_bash() -> ProcessHandle  [abstract]                  │
├─────────────────────────────────────────────────────────────┤
│          ▲                ▲                                │
│          │                │                                │
│  ┌───────┴───────┐ ┌──────┴──────┐                         │
│  │LocalEnvironment│ │SSHEnvironment│                        │
│  └───────────────┘ └──────────────┘                         │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │              FileSyncManager                         │     │
│  │  - upload_fn / delete_fn / bulk_upload_fn           │     │
│  │  - sync() / sync_back()                             │     │
│  └─────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘

注意: Docker/Modal/Daytona/Singularity 等后端在单独规划中,当前范围仅包含 SSH 后端重构

SSHEnvironment 关键流程:
1. __init__ → 建立ControlMaster连接 → 检测remote home → FileSyncManager初始化 → init_session()
2. execute() → _before_execute() [同步文件] → _wrap_command() [注入snapshot/source + CWD marker] → _run_bash() → _wait_for_process() [drain stdout + 中断检查] → _update_cwd() [解析marker] → 返回

会话快照机制:
1. init_session() 执行 bootstrap 脚本，输出到 snapshot_path
2. bootstrap = export -p + declare -f + alias -p + shopt expand_aliases
3. 每次 execute() 前 source snapshot，保持环境变量跨调用持久化

CWD 持久化:
1. _wrap_command() 在命令后注入 printf '\n__HERMES_CWD_<id>%s__HERMES_CWD_<id>\n' "$(pwd -P)"
2. _update_cwd() 解析 stdout 中的 marker，更新 self.cwd
3. 标记前后都有换行，保证 marker 在独立行
```

## Implementation Units

- [ ] **Unit 1: 抽象后端接口设计**

**Goal:** 引入 BaseEnvironment ABC，定义统一后端接口

**Requirements:** R8

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/ssh/base_environment.py`
- Modify: `src/ascend_op_agent/ssh/__init__.py`
- Test: `tests/test_ssh_base.py`

**Approach:**
- 定义 BaseEnvironment 抽象基类
- 声明 execute(), init_session(), cleanup(), _run_bash() 等方法
- 定义 ProcessHandle Protocol（poll/kill/wait/stdout/returncode）
- 定义 _ThreadedProcessHandle 封装类

**Patterns to follow:**
- Hermes `tools/environments/base.py` 的 BaseEnvironment 设计

**Test scenarios:**
- BaseEnvironment 可被正确继承（子类实现所有抽象方法）
- ProcessHandle Protocol 被正确实现（poll/kill/wait/stdout/returncode）
- _ThreadedProcessHandle 正确封装异步执行函数
- execute() 方法签名符合预期 (command, cwd, timeout, stdin_data)
- init_session() 可被调用（不抛异常）
- cleanup() 可被调用（不抛异常）

**Verification:**
- `python -m pytest tests/test_ssh_base.py -v` 通过

---

- [ ] **Unit 2: SSHEnvironment 重构 - ControlMaster + 会话快照**

**Goal:** 实现SSH连接的ControlMaster复用和会话快照机制

**Requirements:** R1, R2, R3

**Dependencies:** Unit 1

**Files:**
- Modify: `src/ascend_op_agent/ssh/manager.py`
- Create: `tests/test_ssh_manager.py` (更新)
- Test: `tests/integration/test_ssh_session.py`

**Approach:**

*SSHManager 重构要点:*
```python
class SSHEnvironment(BaseEnvironment):
    def __init__(self, host, user, port, key_path, cwd, timeout):
        # 1. 建立 ControlMaster 连接
        self._control_socket = Path(tempfile.gettempdir()) / "hermes-ssh" / f"{hash}.sock"
        self._control_socket.parent.mkdir(parents=True, exist_ok=True)
        self._establish_connection()  # ssh -o ControlMaster=auto -o ControlPersist=300

        # 2. 检测远程 home 目录
        self._remote_home = self._detect_remote_home()

        # 3. 初始化 FileSyncManager
        self._sync_manager = FileSyncManager(
            get_files_fn=lambda: iter_sync_files(f"{self._remote_home}/.hermes"),
            upload_fn=self._scp_upload,
            delete_fn=self._ssh_delete,
            bulk_upload_fn=self._ssh_bulk_upload,
            bulk_download_fn=self._ssh_bulk_download,
        )

        # 4. 初始化会话快照
        self.init_session()

    def init_session(self):
        """捕获远程shell环境到快照文件"""
        bootstrap = (
            f"export -p > {self._snapshot_path}\n"
            f"declare -f | grep -vE '^_[^_]' >> {self._snapshot_path}\n"
            f"alias -p >> {self._snapshot_path}\n"
            f"echo 'shopt -s expand_aliases' >> {self._snapshot_path}\n"
        )
        # 执行 bootstrap 并设置 self._snapshot_ready = True

    def _wrap_command(self, command, cwd):
        """包装命令：source snapshot + cd + 执行 + 打印CWD marker"""
        parts = []
        if self._snapshot_ready:
            parts.append(f"source {self._snapshot_path} >/dev/null 2>&1 || true")
        parts.append(f"builtin cd {shlex.quote(cwd)} || exit 126")
        parts.append(f"eval '{escaped_cmd}'")
        if self._snapshot_ready:
            parts.append(f"export -p > {self._snapshot_path} 2>/dev/null || true")
        parts.append(f"printf '\\n{self._cwd_marker}%s{self._cwd_marker}\\n' \"$(pwd -P)\"")
        return "\n".join(parts)

    def _before_execute(self):
        """执行前同步文件"""
        self._sync_manager.sync()

    def cleanup(self):
        """断开ControlMaster连接"""
        self._sync_manager.sync_back()
        # ssh -o ControlPath=... -O exit ...
```

*ControlMaster 命令构建:*
```python
def _build_ssh_command(self, extra_args=None):
    cmd = ["ssh"]
    cmd.extend(["-o", f"ControlPath={self._control_socket}"])
    cmd.extend(["-o", "ControlMaster=auto"])
    cmd.extend(["-o", "ControlPersist=300"])
    cmd.extend(["-o", "BatchMode=yes"])
    cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
    cmd.extend(["-o", "ConnectTimeout=10"])
    if self.port != 22:
        cmd.extend(["-p", str(self.port)])
    if self.key_path:
        cmd.extend(["-i", self.key_path])
    return cmd
```

**Execution note:** 本单元涉及遗留代码重构，建议先添加表征测试覆盖现有行为

**Patterns to follow:**
- Hermes `tools/environments/ssh.py` 的 SSHEnvironment 实现

**Test scenarios:**
- SSH连接使用ControlMaster复用（同一 socket 连续执行多条命令）
- 会话快照正确创建（snapshot 文件包含 export/declare/alias）
- 会话快照正确被 source（环境变量在连续命令中保持）
- 环境变量跨命令保持（设置 TEST_VAR=123 后再次执行 echo $TEST_VAR 应返回 123）
- CWD marker 正确解析（cd /tmp 后 self.cwd 更新为 /tmp）
- CWD marker 不出现在命令输出中
- 连接断开后自动重连（模拟断开场景）
- 快照文件写入冲突时使用 flock 机制
- init_session 失败时回退到 bash -l

**Execution note:** 本单元涉及遗留代码重构，建议先添加表征测试覆盖现有行为

**Verification:**
- `python -m pytest tests/test_ssh_manager.py -v` 通过
- 多命令执行后远程环境变量保持

---

- [ ] **Unit 3: 文件同步优化 - 批量上传 + 增量缓存**

**Goal:** 实现tar-over-SSH批量传输和hash缓存

**Requirements:** R6

**Dependencies:** Unit 2

**Files:**
- Modify: `src/ascend_op_agent/ssh/sync.py`
- Test: `tests/test_file_sync.py`

**Approach:**

*批量上传 (tar-over-SSH):*
```python
def _ssh_bulk_upload(self, files: list[tuple[str, str]]):
    """用 tar | ssh 管道一次性传输大量文件"""
    if not files:
        return

    # 1. 创建临时目录，用符号链接 staged 文件
    with tempfile.TemporaryDirectory(prefix="hermes-ssh-bulk-") as staging:
        for host_path, remote_path in files:
            staged = os.path.join(staging, remote_path.lstrip("/"))
            os.makedirs(os.path.dirname(staged), exist_ok=True)
            os.symlink(os.path.abspath(host_path), staged)

        # 2. tar 打包，通过 SSH 管道传输
        tar_cmd = ["tar", "-chf", "-", "-C", staging, "."]
        ssh_cmd = self._build_ssh_command()
        ssh_cmd.append("tar xf - -C /")

        # 3. 处理管道超时和错误
```

*hash 缓存实现:*
```python
class FileSyncManager:
    def __init__(self, get_files_fn, upload_fn, delete_fn, ...):
        self._pushed_hashes: dict[str, tuple[float, int]] = {}  # path -> (mtime, size)

    def _compute_hash(self, host_path: str) -> tuple[float, int]:
        st = Path(host_path).stat()
        return (st.st_mtime, st.st_size)

    def sync(self, force=False):
        for host_path, remote_path in self.get_files_fn():
            key = f"{host_path}:{remote_path}"
            new_hash = self._compute_hash(host_path)
            if force or key not in self._pushed_hashes or self._pushed_hashes[key] != new_hash:
                self._do_upload(host_path, remote_path)
                self._pushed_hashes[key] = new_hash
```

**Patterns to follow:**
- Hermes `tools/environments/file_sync.py` 的 FileSyncManager 实现

**Test scenarios:**
- 大目录批量上传性能提升（vs 逐文件 scp）
- hash 缓存命中时跳过上传
- force=True 强制重新上传

**Verification:**
- `python -m pytest tests/test_file_sync.py -v` 通过

---

- [ ] **Unit 4: 后台进程处理 + 命令中断支持**

**Goal:** 实现后台进程管理和用户中断支持

**Requirements:** R4, R5

**Dependencies:** Unit 2

**Files:**
- Modify: `src/ascend_op_agent/ssh/manager.py`
- Create: `tests/test_interrupt.py`

**Approach:**

*后台进程处理:*
```python
@staticmethod
def _rewrite_compound_background(cmd_string: str) -> str:
    """
    改写 compound background 命令
    A && B & → A && { B & }  避免 subshell 泄漏
    """
    # 检测并改写 A && B & 模式
    # 将其改写为 A && { B & } 使 B 在当前shell后台运行
```

*命令中断:*
```python
def _wait_for_process(self, proc, timeout=120):
    """轮询进程状态，支持中断检查"""
    while proc.poll() is None:
        if is_interrupted():
            self._kill_process_group(proc)
            return {"output": "...\n[Command interrupted]", "returncode": 130}
        if time.monotonic() > deadline:
            self._kill_process_group(proc)
            return {"output": "...\n[Command timed out]", "returncode": 124}
        time.sleep(0.2)
        # 定期 touch activity callback
```

*进程组 kill:*
```python
def _kill_process_group(self, proc):
    """终止整个进程组（包括子进程）"""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
```

**Patterns to follow:**
- Hermes `tools/environments/base.py` 的 _wait_for_process 实现

**Test scenarios:**
- `long_running_cmd &` 后台执行不被阻塞
- 用户中断时进程组正确终止
- 超时后进程组正确终止

**Verification:**
- `python -m pytest tests/test_interrupt.py -v` 通过

---

- [ ] **Unit 5: RemoteEnvValidator 容器管理实现**

**Goal:** 实现远程环境验证和容器生命周期管理

**Requirements:** R7

**Dependencies:** Unit 2

**Files:**
- Modify: `src/ascend_op_agent/ssh/env_config.py`
- Create: `tests/test_container.py`

**Approach:**

*容器状态检查:*
```python
def check_container_status(self, container_name: str) -> dict:
    """使用 docker inspect 检查容器状态"""
    cmd = self._build_ssh_command()
    cmd.append(f"docker inspect {container_name} --format '{{{{.State.Running}}}}'")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    if result.returncode == 0:
        running = result.stdout.strip().lower() == "true"
        return {"exists": True, "running": running, "status": "running" if running else "stopped"}
    return {"exists": False, "running": False, "status": "unknown"}
```

*容器创建:*
```python
def create_container(self, image_name: str, container_name: str) -> bool:
    """使用 docker run 创建容器"""
    create_cmd = f"docker run -d --name {container_name} {image_name} sleep infinity"
    cmd = self._build_ssh_command()
    cmd.append(create_cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0
```

*容器启动/停止:*
```python
def start_container(self, container_name: str) -> bool:
    cmd = self._build_ssh_command()
    cmd.append(f"docker start {container_name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode == 0

def stop_container(self, container_name: str) -> bool:
    cmd = self._build_ssh_command()
    cmd.append(f"docker stop {container_name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode == 0
```

**Patterns to follow:**
- Hermes `tools/environments/docker.py` 的容器管理

**Test scenarios:**
- 容器状态检测正确（存在/不存在/运行中/已停止）
- 容器创建成功
- 容器启动/停止成功

**Verification:**
- `python -m pytest tests/test_container.py -v` 通过

---

- [ ] **Unit 6: CWD 持久化 + stdin 处理**

**Goal:** 完善 CWD 追踪和 stdin 传递

**Requirements:** R2

**Dependencies:** Unit 2

**Files:**
- Modify: `src/ascend_op_agent/ssh/manager.py`
- Test: `tests/test_cwd_tracking.py`

**Approach:**

*CWD 标记解析:*
```python
def _extract_cwd_from_output(self, result: dict):
    """解析 stdout 中的 CWD 标记，更新 self.cwd"""
    output = result.get("output", "")
    marker = self._cwd_marker

    last = output.rfind(marker)
    if last == -1:
        return

    # 找到匹配的开标记（在last之前）
    search_start = max(0, last - 4096)  # CWD 路径不会超过 4KB
    first = output.rfind(marker, search_start, last)
    if first == -1 or first == last:
        return

    cwd_path = output[first + len(marker):last].strip()
    if cwd_path:
        self.cwd = cwd_path

    # 从输出中移除 marker 行
    line_start = output.rfind("\n", 0, first)
    if line_start == -1:
        line_start = first
    line_end = output.find("\n", last + len(marker))
    line_end = line_end + 1 if line_end != -1 else len(output)
    result["output"] = output[:line_start] + output[line_end:]
```

*stdin 传递:*
```python
def exec_command(self, cmd: str, timeout: int = 300, stdin_data: str = None) -> CommandResult:
    """支持 stdin_data 传递"""
    wrapped = self._wrap_command(cmd, self.cwd)

    if stdin_data:
        # 通过 pipe 传递 stdin
        proc = subprocess.Popen(
            self._build_ssh_command() + ["bash", "-c", shlex.quote(wrapped)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
        )
        proc.stdin.write(stdin_data)
        proc.stdin.close()
    else:
        proc = subprocess.Popen(...)
```

**Test scenarios:**
- cd 后下一个命令在正确目录执行
- marker 不出现在最终输出中
- stdin_data 正确传递到命令

**Verification:**
- `python -m pytest tests/test_cwd_tracking.py -v` 通过

---

## System-Wide Impact

### Backward Compatibility

**迁移策略:**
- 初期: SSHManager 和 SSHEnvironment 并存，SSHManager 作为 SSHEnvironment 的包装器
- 渐进式: 逐步将 SSHManager 的调用方迁移到 SSHEnvironment
- 最终: SSHManager 标记为 deprecated，统一使用 SSHEnvironment

**迁移步骤:**
1. Unit 1 创建 BaseEnvironment 接口
2. Unit 2 实现 SSHEnvironment，实现 BaseEnvironment 接口
3. SSHManager 实现 `BaseEnvironment` 接口，作为 SSHEnvironment 的代理
4. 现有代码无需修改，通过接口调用 SSHManager 即可自动获得新功能

### Interaction Graph

| 组件 | 影响 |
|------|------|
| SSHManager | 被 SSHEnvironment 替代（渐进式） |
| FileSync | 与 FileSyncManager 合并 |
| RemoteEnvValidator | 实现完整容器管理 |
| Workflow Phases | 无需修改（接口保持兼容） |

### Error Propagation

- SSH 连接失败 → 抛出 SSHConnectionError，包含原因（认证失败/超时/拒绝）
- 命令执行超时 → 返回 returncode=124 + 超时消息
- 用户中断 → 返回 returncode=130 + "[Command interrupted]"
- 文件同步失败 → 回退到逐文件上传，记录警告

### State Lifecycle Risks

- 连接断开时 snapshot 文件可能过期 → 重新执行 init_session
- 多命令并发执行时 snapshot 写入冲突 → 使用文件锁
- 容器删除后 snapshot 残留 → cleanup 时清理

## Risks & Dependencies

| 风险 | 影响 | 缓解 |
|------|------|------|
| ControlMaster socket 路径超限 | macOS 上路径超 104 字节限制 | 使用 hash 缩短路径 |
| 多 shell 竞争 snapshot 文件 | 多命令并发时环境变量丢失 | 使用 flock（文件锁）+ 写入前检查 + 重试机制；避免死锁：锁作用域短，写入后立即释放 |
| 大文件上传超时 | tar-over-SSH 可能挂起 | 添加 timeout 和重试 |
| Docker daemon 无响应 | 容器操作阻塞 | 使用 subprocess timeout |

## Phased Delivery

### Phase 1: 接口抽象（Unit 1）
- BaseEnvironment ABC 定义
- ProcessHandle Protocol
- **目标**: 调用方可基于抽象接口编程

### Phase 2: SSH 重构（Unit 2 + Unit 6）
- ControlMaster 连接复用
- 会话快照机制
- CWD 持久化
- **目标**: SSH 后端行为与 Hermes 一致

### Phase 3: 文件同步优化（Unit 3）
- tar-over-SSH 批量上传
- hash 缓存实现
- **目标**: 大目录同步性能提升 10x

### Phase 4: 进程管理（Unit 4）
- 后台进程处理
- 命令中断支持
- **目标**: 支持长时间运行的命令和用户中断

### Phase 5: 容器管理（Unit 5）
- RemoteEnvValidator 实现
- 容器生命周期管理
- **目标**: 支持容器化远程开发

## Documentation / Operational Notes

- 更新 `docs/architecture.md` 中的 SSH 模块设计
- 更新 `docs/config.md` 中的远程环境配置说明
- 添加 SSH 故障排查指南（ControlMaster 问题、snapshot 问题）

## Sources & References

- **Origin document:** [docs/brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md](../brainstorms/2026-04-29-ascend-op-from-scratch-workflow-requirements.md)
- **Hermes Agent SSH 实现:** `/Users/huangshilei/Documents/pythonprojects/hermes-agent/tools/environments/ssh.py`
- **Hermes Agent BaseEnvironment:** `/Users/huangshilei/Documents/pythonprojects/hermes-agent/tools/environments/base.py`
- **Hermes Agent FileSyncManager:** `/Users/huangshilei/Documents/pythonprojects/hermes-agent/tools/environments/file_sync.py`