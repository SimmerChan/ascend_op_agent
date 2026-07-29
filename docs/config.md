# 配置参考

## 文档索引

- [LLM Provider 配置详解](modules/llm_providers.md) - 支持的 Provider、API Key 配置、模型选择
- [MCP 服务器配置](modules/mcp.md) - MCP 服务器类型和配置
- [SSH 远程开发](modules/ssh.md) - 远程环境配置
- [Skills 配置](modules/skills.md) - Skill 仓库配置

## 双文件架构

| 文件 | 内容 | 版本控制 |
|------|------|----------|
| `~/.ascend_op_agent/config.yaml` | 行为配置（LLM 模型、MCP 服务器、checkpoint 路径等） | 可提交 |
| `~/.ascend_op_agent/.env` | 敏感凭据（API Key、SSH 密码等） | 不提交（chmod 600） |

`config.yaml` 中用 `${ENV_VAR}` 引用环境变量，也支持带默认值 `${ENV_VAR:-default}` 语法（由 `security/token_resolver.py` 解析）。

## LLM 三 Provider 轮询

LLM 配额/服务频繁踩坑，已配三个 provider 互为备份（**全部走 Anthropic 兼容协议**）。调试时任一 provider 不可用（配额耗尽 / 服务波动 / timeout），按轮询顺序切下一个 provider 重试，不原地重试同一 provider。

**轮询顺序**：Minimax（主力）-> 智谱 GLM-5.2（备 1）-> 火山 Ark GLM-5.2（备 2）-> 三者都挂则停止 LLM 依赖操作并报告用户。

| Provider | Model | api_base | Key env | 备注 |
|---------|-------|----------|---------|------|
| **Minimax**（默认主力） | `MiniMax-M3` | `https://api.minimaxi.com/anthropic` | `MINIMAX_API_KEY` | N=20 stress 100% PASS |
| **智谱 GLM-5.2**（备 1） | `glm-5.2` | `https://open.bigmodel.cn/api/anthropic` | `GLM_API_KEY` | 不适合 stress（连续调用 timeout） |
| **火山 Ark GLM-5.2**（备 2） | `glm-5.2` | `https://ark.cn-beijing.volces.com/api/plan` | `ARK_API_KEY` | 走 Bearer auth（用 `auth_token`，非 `api_key`） |

### 配置示例

```yaml
# Minimax (默认主力, Anthropic 兼容)
llm:
  provider: "anthropic"
  api_key: "${MINIMAX_API_KEY}"
  api_base: "https://api.minimaxi.com/anthropic"
  model: "MiniMax-M3"

# 智谱 GLM-5.2 (备 1, Anthropic 兼容)  - 取消注释切换
# llm:
#   provider: "anthropic"
#   api_key: "${GLM_API_KEY}"
#   api_base: "https://open.bigmodel.cn/api/anthropic"
#   model: "glm-5.2"

# 火山 Ark GLM-5.2 (备 2, Anthropic 兼容)  - 取消注释切换
# llm:
#   provider: "anthropic"
#   auth_token: "${ARK_API_KEY}"   # Ark 走 Bearer；api_key 的 x-api-key 会 401
#   api_base: "https://ark.cn-beijing.volces.com/api/plan"
#   model: "glm-5.2"
```

### adapter streaming + per-provider max_tokens

- **streaming 总开**：adapter 走 `messages.stream` + `get_final_message`，绕过 anthropic SDK >32K non-stream 10min 长请求保护；tool_use 流式聚合三端点实证完整
- **per-provider max_tokens 自适应上限**（`providers/base.py` 的 `PROVIDER_MAX_TOKENS`，按 `api_base` 子串匹配）：Minimax 256K / GLM 官方 128K / Ark 64K
- `LLMConfig.max_tokens` 默认 `None` = 用 provider 真实上限；填数字 = `min(数字, 上限)`
- `disable_thinking` 默认 false（不禁推理，保留 thinking；glm-5.2 是推理模型）

> **教训**：LLM 返回空必先看 raw response（`stop_reason` / thinking vs text / `usage.output_tokens`）；max_tokens 上限以实测为准，别信"模型支持 1M"宣称（三端点实测 1048576 全 400 拒绝）。

## 编排器 checkpoint

```yaml
checkpoint:
  db_path: ~/.ascend_op_agent/checkpoints.db
  auto_resume: true   # 启动时检测未完成 thread 并提示 resume
```

`CheckpointStore`（SQLite v2，三表：checkpoints / artifacts / pending_approvals）+ WAL + busy_timeout=30000 + v1↔v2 自动迁移。

## 远程开发环境（910B SSH）

```yaml
remote:
  host: ${NPU_HOST}          # 192.168.9.105
  user: ${NPU_USER}          # root
  port: 22
  key_path: ${NPU_KEY_PATH:-}   # 免密登录时留空
  password: ${NPU_PASSWORD:-}
  container_name: "ops_pt"   # 910B 在 docker 容器里时填写（NpuExecutor 自动包 docker exec）
```

> 非交互 SSH 不加载 CANN env，`NpuExecutor.remote_env_setup` 自动前缀 `source /usr/local/Ascend/ascend-toolkit/set_env.sh &&`。

## MCP 服务器配置

```yaml
mcp:
  servers:
    - name: code-search
      type: stdio
      command: npx /path/to/server
```

### MCP 服务器类型

- `stdio`: 标准输入输出模式
- `http`: HTTP 轮询模式
- `streamable-http`: 可流式 HTTP 模式

## 本地模式

```yaml
local:
  workspace: "./workspace"
  skills_path: "~/.ascend_op_agent/skills"
```