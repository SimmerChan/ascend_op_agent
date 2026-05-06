# 配置参考

## 文档索引

- [LLM Provider 配置详解](modules/llm_providers.md) - 支持的 Provider、API Key 配置、模型选择
- [MCP 服务器配置](modules/mcp.md) - MCP 服务器类型和配置
- [SSH 远程开发](modules/ssh.md) - 远程环境配置
- [Skills 配置](modules/skills.md) - Skill 仓库配置

## 配置文件结构

```yaml
# LLM 配置（详见 LLM Provider 配置文档）
llm:
  provider: "openai"
  api_base: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o"

# MCP 服务器配置
mcp:
  servers:
    - name: code-search
      type: stdio
      command: npx /path/to/server

# 远程开发环境配置
remote:
  host: "192.168.1.100"
  user: "root"
  key_path: "~/.ssh/id_rsa"

# 本地模式
local:
  workspace: "./workspace"
  skills_path: "~/.ascend_op_agent/skills"
```

## 环境变量引用

支持 `${ENV_VAR}` 格式引用环境变量，也支持带默认值 `${ENV_VAR:-default}` 语法。

详见 [LLM Provider 配置详解](modules/llm_providers.md)。

## MCP服务器类型

- `stdio`: 标准输入输出模式
- `http`: HTTP轮询模式
- `streamable-http`: 可流式HTTP模式