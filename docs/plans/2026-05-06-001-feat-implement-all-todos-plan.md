---
title: "feat: 实现项目所有 TODO"
type: feat
status: active
date: 2026-05-06
---

# 实现项目所有 TODO

## Summary

实现 Ascend Op Agent 项目中的 9 个 TODO 项，涵盖 LLM 多 Provider 支持、MCP 服务器生命周期管理、SSH 文件同步、环境可用性检查和算子调用实现。参考 Hermes Agent 的设计方案。

## Problem Frame

当前 Ascend Op Agent 存在以下未完成的功能：

1. **LLM 客户端为 stub** - `LLMClient.call()` 返回 "TODO: 实现LLM调用"，Agent 无法真正与 LLM 通信
2. **MCP 命令未实现** - `ascend-op-agent mcp start/stop/status` 只有占位输出
3. **SSH 同步功能缺失** - `ascend-op-agent sync push/pull` 只有占位输出
4. **环境检查不完整** - `RemoteEnvValidator.check_environment()` 未实际检查环境可用性
5. **算子调用为 stub** - PyTorch/TensorFlow 算子封装中的 "// TODO: 实现实际的算子调用"

## Requirements

- R1. 实现多 Provider LLM 客户端，支持 OpenAI GPT-4 和 Anthropic Claude
- R2. 实现 MCP 服务器生命周期管理（启动/停止/状态检查）
- R3. 实现 SSH 文件同步功能（push 本地到远程，pull 远程到本地）
- R4. 实现环境可用性实际检查（SSH 连接测试）
- R5. 实现算子调用（PyTorch/TensorFlow 封装）

## Scope Boundaries

**包含:**
- `src/ascend_op_agent/agent/core.py` - LLMClient 多 Provider 实现
- `src/ascend_op_agent/mcp/lifecycle.py` - CLI 集成
- `src/ascend_op_agent/ssh/manager.py` - sync_files 方法实现
- `src/ascend_op_agent/ssh/env_config.py` - 环境检查实现
- `src/ascend_op_agent/workflow/adapters.py` - 算子调用实现
- 相关测试文件

**不包含:**
- 前端 Ink raw mode 问题修复（单独问题）
- 其他非 TODO 功能开发

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/agent/core.py` - 当前 LLMClient stub 实现
- `src/ascend_op_agent/mcp/lifecycle.py` - MCPLifecycleManager 已有实现
- `src/ascend_op_agent/ssh/manager.py` - SSHManager（deprecated）
- `src/ascend_op_agent/ssh/sync.py` - FileSyncManager 已有实现
- `src/ascend_op_agent/ssh/env_config.py` - RemoteEnvValidator 当前实现
- `src/ascend_op_agent/workflow/adapters.py` - 算子代码生成模板

### Hermes Agent 参考实现

**LLM Adapter 模式:**
- `hermes-agent/agent/anthropic_adapter.py` - Anthropic API 适配器
- `hermes-agent/agent/bedrock_adapter.py` - AWS Bedrock 适配器
- `hermes-agent/agent/gemini_cloudcode_adapter.py` - Google Cloud Code 适配器

**MCP Lifecycle 模式:**
- `hermes-agent/tools/mcp_tool.py` - MCP 服务器生命周期管理
- `hermes-agent/hermes_cli/mcp_config.py` - MCP 配置管理

**SSH Sync 模式:**
- `hermes-agent/tools/environments/ssh.py` - SSHEnvironment 实现
- Unit 2-3 定义于 `docs/plans/2026-05-01-001-refactor-ssh-environment-plan.md`

## Key Technical Decisions

- **D1 - LLM 适配器模式**: 每个 Provider 独立适配器类，统一接口。**理由**: Hermes Agent 验证的模式，便于扩展新 Provider
- **D2 - MCP CLI 集成**: 直接调用 `MCPLifecycleManager` 实例方法。**理由**: 避免重复启动进程
- **D3 - SSH 同步基于现有 FileSyncManager**: 使用 `sync()` 和 `sync_back()` 方法。**理由**: 减少重复代码
- **D4 - 环境检查复用 SSH 连接**: 在 `check_environment()` 中测试实际 SSH 连接。**理由**: 确保环境真正可用

## Implementation Units

- U1. **LLM 多 Provider 客户端实现**

**Goal:** 实现支持 OpenAI GPT-4 和 Anthropic Claude 的 LLM 客户端

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/agent/core.py`
- Create: `src/ascend_op_agent/agent/providers/__init__.py`
- Create: `src/ascend_op_agent/agent/providers/base.py`
- Create: `src/ascend_op_agent/agent/providers/openai_adapter.py`
- Create: `src/ascend_op_agent/agent/providers/anthropic_adapter.py`
- Create: `tests/test_llm_providers.py`

**Approach:**
```
LLMClient.call() 流程:
1. 根据 config.provider 选择适配器
2. 适配器执行实际 API 调用
3. 处理重试、超时、错误
4. 返回格式化响应
```

**Patterns to follow:**
- Hermes `hermes-agent/agent/anthropic_adapter.py` 适配器模式

**Test scenarios:**
- Happy path: OpenAI GPT-4 成功调用并返回响应
- Happy path: Anthropic Claude 成功调用并返回响应
- Edge case: API key 无效时返回认证错误
- Edge case: 网络超时触发重试
- Edge case: rate limit 触发指数退避
- Error path: 不支持的 provider 抛出异常

**Verification:**
- `python -m pytest tests/test_llm_providers.py -v` 通过
- Agent 能真正与 LLM 对话并获得响应

---

- U2. **MCP CLI 生命周期管理集成**

**Goal:** 实现 `ascend-op-agent mcp start/stop/status` 命令

**Requirements:** R2

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/cli.py` (lines 324-346)
- Modify: `src/ascend_op_agent/mcp/lifecycle.py`
- Create: `tests/test_mcp_cli.py`

**Approach:**
```
mcp start:
1. 从 config 读取 MCP 服务器配置
2. 调用 MCPLifecycleManager.start_server()
3. 输出启动结果

mcp stop:
1. 调用 MCPLifecycleManager.stop_server()
2. 输出停止结果

mcp status:
1. 遍历所有配置的服务器
2. 调用 health_check() 获取状态
3. 输出状态表格
```

**Patterns to follow:**
- Hermes `hermes-agent/tools/mcp_tool.py` 的生命周期管理

**Test scenarios:**
- Happy path: start 命令启动 stdio MCP 服务器
- Happy path: start 命令配置 HTTP MCP 服务器（无进程启动）
- Happy path: stop 命令正确停止运行的服务器
- Happy path: status 命令显示所有服务器状态
- Edge case: 启动不存在的服务器配置
- Edge case: 停止已停止的服务器（幂等）

**Verification:**
- `ascend-op-agent mcp list` 列出已配置服务器
- `ascend-op-agent mcp start <name>` 启动服务器
- `ascend-op-agent mcp stop <name>` 停止服务器
- `ascend-op-agent mcp status` 显示状态

---

- U3. **SSH 文件同步实现**

**Goal:** 实现 `ascend-op-agent sync push/pull` 命令

**Requirements:** R3

**Dependencies:** U1, U2 (需要 SSH 配置就绪)

**Files:**
- Modify: `src/ascend_op_agent/cli.py` (lines 398-428)
- Modify: `src/ascend_op_agent/ssh/manager.py`
- Modify: `src/ascend_op_agent/ssh/sync.py`
- Create: `tests/test_sync_cli.py`

**Approach:**
```
sync push:
1. 遍历 --files 指定的文件/目录
2. 调用 FileSyncManager.sync() 上传到远程
3. 输出同步结果（文件数、字节数）

sync pull:
1. 调用 FileSyncManager.sync_back() 下载到本地
2. 输出同步结果
```

**Patterns to follow:**
- Hermes `hermes-agent/tools/environments/ssh.py` FileSyncManager 使用方式

**Test scenarios:**
- Happy path: push 单个文件成功
- Happy path: push 目录（递归）成功
- Happy path: pull 远程文件到本地
- Edge case: 指定不存在的文件报错
- Edge case: 网络中断触发重试
- Error path: 远程目录无写权限

**Verification:**
- `ascend-op-agent sync push -f src/ops` 推送文件到远程
- `ascend-op-agent sync pull -f src/ops` 从远程拉取

---

- U4. **环境可用性检查实现**

**Goal:** 实现 RemoteEnvValidator.check_environment() 实际检查

**Requirements:** R4

**Dependencies:** U3

**Files:**
- Modify: `src/ascend_op_agent/ssh/env_config.py` (line 193)
- Create: `tests/test_env_validator.py`

**Approach:**
```
check_environment():
1. 使用 SSHManager 尝试连接到远程环境
2. 执行简单命令（如 echo test）验证连接
3. 检查 Docker 容器是否运行（如果配置了容器）
4. 返回详细的连接状态信息
```

**Patterns to follow:**
- Hermes `hermes-agent/tools/environments/ssh.py` 连接验证

**Test scenarios:**
- Happy path: SSH 连接成功时返回环境信息
- Edge case: SSH 连接超时
- Edge case: Docker 容器未运行
- Error path: 认证失败

**Verification:**
- RemoteEnvValidator 能正确报告环境连接状态

---

- U5. **算子调用实现**

**Goal:** 实现 PyTorch/TensorFlow 算子封装中的实际调用逻辑

**Requirements:** R5

**Dependencies:** None

**Files:**
- Modify: `src/ascend_op_agent/workflow/adapters.py` (lines 143, 373)
- Create: `tests/test_operator_calls.py`

**Approach:**
```
PyTorch 算子:
1. 调用 PyTorch ACL (Ascend C) 接口
2. 使用 torch.ops.ascend 调用算子
3. 处理输入/输出 Tensor 转换

TensorFlow 算子:
1. 调用 TF Lite 运行时或 Ascend TF 适配
2. 使用 tf.numpy_function 包装算子调用
3. 处理 GPU/CPU 内存管理
```

**Patterns to follow:**
- 昇腾官方 PyTorch ACL 文档

**Test scenarios:**
- Happy path: PyTorch 算子前向传播正确
- Happy path: TensorFlow 算子返回正确 shape
- Edge case: 输入 Tensor shape 不匹配
- Edge case: 内存不足

**Verification:**
- 生成的算子代码能正确调用 Ascend NPU

## System-Wide Impact

- **LLM Provider 切换**: `config.py` 中 LLMConfig 需支持多 Provider
- **MCP 健康检查**: MCPLifecycleManager 已在运行时的健康检查线程

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| LLM API 兼容性问题 | 参考 Hermes 适配器模式，使用 httpx |
| SSH 同步大文件超时 | 实现断点续传和增量同步 |
| MCP 服务器启动失败 | 增加超时和错误处理 |
| 算子调用平台依赖 | 检查 Ascend ACL 是否可用 |

## Documentation / Operational Notes

- 更新 README.md 说明支持的 LLM Provider
- 添加 MCP 服务器配置示例
- 添加 SSH 同步使用说明
