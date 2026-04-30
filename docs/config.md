# 配置参考

## 配置文件结构

```yaml
# LLM 配置
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

支持 `${ENV_VAR}` 格式引用环境变量

## MCP服务器类型

- `stdio`: 标准输入输出模式
- `http`: HTTP轮询模式
- `streamable-http`: 可流式HTTP模式