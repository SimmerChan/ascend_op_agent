# LLM Provider 配置指南

Ascend Op Agent 支持多 LLM Provider，通过适配器模式实现。目前支持以下 Providers：

| Provider | 模型 | 说明 |
|----------|------|------|
| `openai` | GPT-4o, GPT-4-turbo, GPT-3.5-turbo 等 | OpenAI ChatGPT API |
| `anthropic` | Claude Sonnet 4, Claude Opus 4, Claude Haiku 等 | Anthropic Claude API |
| `gemini` | Gemini 2.5 Flash, Gemini 2.0 Pro 等 | Google Gemini API |
| `openrouter` | 150+ 模型 | OpenRouter 聚合网关 |
| `azure` | GPT-4o, GPT-4-turbo 等 | Azure OpenAI |
| `ollama` | Llama 3, Mistral, Qwen 等 | 本地 Ollama 模型 |

## 快速配置

### 方式一：使用 .env 文件（推荐）

创建 `~/.ascend_op_agent/.env` 文件存储敏感凭据：

```bash
mkdir -p ~/.ascend_op_agent
```

添加 API Key：

```bash
# OpenAI
OPENAI_API_KEY=sk-your-openai-key

# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key

# Google Gemini
GEMINI_API_KEY=your-gemini-key

# OpenRouter
OPENROUTER_API_KEY=sk-or-your-openrouter-key

# Azure OpenAI
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
```

### 方式二：直接在 config.yaml 中配置

创建 `~/.ascend_op_agent/config.yaml`：

```yaml
llm:
  provider: "openai"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o"
```

## OpenAI Provider

### 完整配置项

```yaml
llm:
  provider: "openai"              # 必填，固定值
  api_key: "${OPENAI_API_KEY}"   # 必填，API密钥
  api_base: "https://api.openai.com/v1"  # 可选，默认 OpenAI API 地址
  model: "gpt-4o"                # 可选，默认 gpt-4o
  max_retries: 3                 # 可选，默认 3 次
  timeout: 120                   # 可选，默认 120 秒
```

### 支持的模型

- `gpt-4o` - 最新 GPT-4o，支持视觉和高速响应
- `gpt-4o-mini` - 轻量级 GPT-4o，成本更低
- `gpt-4-turbo` - GPT-4 Turbo，高智能
- `gpt-4` - 原版 GPT-4
- `gpt-3.5-turbo` - 经济实惠的选择

### OpenAI API 兼容网关

如果你使用第三方 OpenAI 兼容 API（如 Cloudflare AI Gateway、LocalAI 等），修改 `api_base`：

```yaml
llm:
  provider: "openai"
  api_key: "your-api-key"
  api_base: "https://api.cloudflare.com/v1/your-account-id/openai"
  model: "gpt-4o"
```

## Anthropic Provider

### 完整配置项

```yaml
llm:
  provider: "anthropic"          # 必填，固定值
  api_key: "${ANTHROPIC_API_KEY}" # 必填，API密钥
  model: "claude-sonnet-4-6-20250514"  # 可选，默认 Claude Sonnet 4
  max_retries: 3                 # 可选，默认 3 次
  timeout: 120                   # 可选，默认 120 秒
```

### 支持的模型

- `claude-sonnet-4-6-20250514` - Claude Sonnet 4，最新高性能模型
- `claude-opus-4-6-20250514` - Claude Opus 4，最强智能
- `claude-haiku-4-6-20250514` - Claude Haiku，快速响应

### Anthropic API 地址

Anthropic API 使用 `api_base: "https://api.anthropic.com"`，默认已配置，无需修改。

## Google Gemini Provider

### 完整配置项

```yaml
llm:
  provider: "gemini"             # 必填，固定值
  api_key: "${GEMINI_API_KEY}"  # 必填，API密钥
  api_base: "https://generativelanguage.googleapis.com/v1beta"  # 可选
  model: "gemini-2.5-flash"     # 可选，默认 gemini-2.5-flash
  max_retries: 3                # 可选，默认 3 次
  timeout: 120                  # 可选，默认 120 秒
```

### 支持的模型

- `gemini-2.5-flash` - 最新高性能模型，推荐
- `gemini-2.5-pro` - 最强推理能力
- `gemini-2.0-flash` - 稳定版本
- `gemini-1.5-pro` - 长上下文支持
- `gemini-1.5-flash` - 经济实惠

## OpenRouter Provider

OpenRouter 是一个聚合网关，通过单一 API 访问 150+ LLM 模型（Anthropic、OpenAI、Google、Meta、Mistral 等）。

### 完整配置项

```yaml
llm:
  provider: "openrouter"                 # 必填，固定值
  api_key: "${OPENROUTER_API_KEY}"       # 必填，API密钥
  api_base: "https://openrouter.ai/api/v1"  # 可选，已是默认
  model: "anthropic/claude-sonnet-4-6-20250514"  # 推荐模型
  max_retries: 3                        # 可选
  timeout: 120                          # 可选
```

### 热门模型

| 模型 | 说明 |
|------|------|
| `anthropic/claude-sonnet-4-6-20250514` | Claude Sonnet 4，推荐 |
| `anthropic/claude-opus-4-6-20250514` | Claude Opus 4，最强 |
| `openai/gpt-4o` | GPT-4o |
| `google/gemini-2.5-flash` | Gemini 2.5 Flash |
| `meta-llama/llama-3-70b-instruct` | Llama 3 70B |
| `mistralai/mistral-large` | Mistral Large |

### 获取 OpenRouter API Key

1. 访问 [openrouter.ai](https://openrouter.ai)
2. 注册并获取 API Key
3. 充值余额（按使用量计费）

## Azure OpenAI Provider

### 完整配置项

```yaml
llm:
  provider: "azure"                        # 必填，固定值
  api_key: "${AZURE_OPENAI_API_KEY}"       # 必填，API密钥
  api_base: "https://your-resource.openai.azure.com"  # 必填，资源端点
  model: "gpt-4o"                          # 部署名称
  api_version: "2024-02-01"               # 可选，默认版本
  max_retries: 3                           # 可选
  timeout: 120                             # 可选
```

### 示例配置

```yaml
llm:
  provider: "azure"
  api_key: "your-azure-key"
  api_base: "https://my-resource.openai.azure.com"
  model: "gpt-4o"
  api_version: "2024-02-01"
```

## Ollama Provider（本地模型）

Ollama 允许在本地运行 LLM 模型，无需互联网连接。

### 安装 Ollama

```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh

# 然后拉取模型
ollama pull llama3
ollama pull mistral
ollama pull qwen2.5
```

### 完整配置项

```yaml
llm:
  provider: "ollama"                      # 必填，固定值
  api_base: "http://localhost:11434/v1"   # 可选，已是默认
  model: "llama3"                         # 必填，本地模型名
  api_key: ""                             # 通常留空
  max_retries: 3                          # 可选
  timeout: 300                             # 可选，本地模型可能较慢
```

### 常用模型

| 模型 | 命令 |
|------|------|
| Llama 3 | `ollama pull llama3` |
| Mistral | `ollama pull mistral` |
| Qwen 2.5 | `ollama pull qwen2.5:7b` |
| Phi-3 | `ollama pull phi3` |
| Gemma | `ollama pull gemma2` |

## 环境变量引用

配置文件中支持 `${ENV_VAR}` 和 `${ENV_VAR:-default}` 语法：

```yaml
# 基本引用，变量不存在时替换为空字符串
api_key: "${OPENAI_API_KEY}"

# 带默认值，变量不存在时使用默认值
model: "${MODEL:-gpt-4o}"
```

## Provider 切换

只需修改 `config.yaml` 中的 `provider` 字段即可切换：

```yaml
# 切换到 Anthropic
llm:
  provider: "anthropic"
  api_key: "${ANTHROPIC_API_KEY}"
  model: "claude-sonnet-4-6-20250514"

# 切换到 OpenRouter
llm:
  provider: "openrouter"
  api_key: "${OPENROUTER_API_KEY}"
  model: "anthropic/claude-sonnet-4-6-20250514"

# 切换到本地 Ollama
llm:
  provider: "ollama"
  model: "llama3"
```

## 完整配置示例

### OpenAI（生产环境）

```yaml
llm:
  provider: "openai"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o"
  api_base: "https://api.openai.com/v1"
  max_retries: 3
  timeout: 120
```

### Anthropic

```yaml
llm:
  provider: "anthropic"
  api_key: "${ANTHROPIC_API_KEY}"
  model: "claude-sonnet-4-6-20250514"
  max_retries: 3
  timeout: 120
```

### OpenRouter（多模型）

```yaml
llm:
  provider: "openrouter"
  api_key: "${OPENROUTER_API_KEY}"
  model: "anthropic/claude-sonnet-4-6-20250514"
  max_retries: 3
  timeout: 120
```

### Azure OpenAI（企业）

```yaml
llm:
  provider: "azure"
  api_key: "${AZURE_OPENAI_API_KEY}"
  api_base: "https://your-resource.openai.azure.com"
  model: "gpt-4o"
  api_version: "2024-02-01"
  max_retries: 3
  timeout: 120
```

### Ollama（本地开发）

```yaml
llm:
  provider: "ollama"
  api_base: "http://localhost:11434/v1"
  model: "llama3"
  timeout: 300
```

### Gemini（备用）

```yaml
llm:
  provider: "gemini"
  api_key: "${GEMINI_API_KEY}"
  model: "gemini-2.5-flash"
  max_retries: 3
  timeout: 120
```

## 错误排查

### OpenAI

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `Illegal header value b'Bearer '` | API Key 为空 | 确保 `.env` 文件中有 `OPENAI_API_KEY` |
| `401 Authentication failed` | API Key 无效 | 检查 API Key 是否正确 |
| `429 Rate limit exceeded` | 请求频率超限 | 等待后重试，或设置 `max_retries` |

### Anthropic

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `AuthenticationError` | API Key 无效 | 检查 `ANTHROPIC_API_KEY` 是否正确 |

### OpenRouter

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `401` | API Key 无效 | 检查 `OPENROUTER_API_KEY` |
| `insufficient credits` | 余额不足 | 充值 OpenRouter 账户 |

### Ollama

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `Connection refused` | Ollama 未运行 | 运行 `ollama serve` |
| `Model not found` | 模型未下载 | 运行 `ollama pull <model>` |

## 添加新的 Provider

如需添加新 Provider，只需创建新的 Adapter 类：

1. 在 `src/ascend_op_agent/agent/providers/` 创建 `xxx_adapter.py`
2. 继承 `BaseLLMAdapter`
3. 实现 `complete()` 和 `get_provider_name()` 方法
4. 在 `core.py` 的 `_ADAPTERS` 字典中注册
5. 在 `__init__.py` 中导出

参见现有实现：
- [OpenAI Adapter 源码](../src/ascend_op_agent/agent/providers/openai_adapter.py)
- [Anthropic Adapter 源码](../src/ascend_op_agent/agent/providers/anthropic_adapter.py)
- [Gemini Adapter 源码](../src/ascend_op_agent/agent/providers/gemini_adapter.py)
- [OpenRouter Adapter 源码](../src/ascend_op_agent/agent/providers/openrouter_adapter.py)
- [Azure Adapter 源码](../src/ascend_op_agent/agent/providers/azure_adapter.py)
- [Ollama Adapter 源码](../src/ascend_op_agent/agent/providers/ollama_adapter.py)