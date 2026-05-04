# 安全策略

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

## 敏感信息管理

### 双文件配置架构

Ascend Op Agent 采用双文件配置架构分离敏感信息：

| 文件 | 用途 | 版本控制 |
|------|------|----------|
| `config.yaml` | 行为配置（LLM模型、MCP服务器等） | ✅ 可提交 |
| `~/.ascend_op_agent/.env` | 敏感凭据（API密钥、Token等） | ❌ 禁止提交 |

### 环境变量引用

配置文件中支持两种环境变量引用语法：

```yaml
# 无默认值 - 环境变量不存在时替换为空
api_key: "${OPENAI_API_KEY}"

# 有默认值 - 环境变量不存在时使用默认值
model: "${EMBEDDING_MODEL:-sentence-transformers/all-MiniLM-L6-v3}"
```

### .env 文件权限

```bash
# 设置受限权限
chmod 600 ~/.ascend_op_agent/.env
```

## 凭据管理

### CredentialManager

`src/ascend_op_agent/security/credential_manager.py` 提供凭据解析功能：

```python
from ascend_op_agent.security.credential_manager import CredentialManager

manager = CredentialManager()
password = manager.get_ssh_password("${SSH_PASSWORD}")
key_path = manager.get_ssh_key_path("${SSH_KEY_PATH}")
```

**安全特性：**
- 支持 `${ENV_VAR}` 格式的环境变量引用
- 自动解析环境变量值
- 不在日志中暴露敏感信息

### TokenResolver

`src/ascend_op_agent/security/token_resolver.py` 提供 Token 解析功能：

```python
from ascend_op_agent.security.token_resolver import TokenResolver

resolver = TokenResolver()
auth_header = resolver.get_auth_header("${MCP_BEARER_TOKEN}")
```

**安全特性：**
- Bearer Token 自动格式化
- 认证头自动生成
- 环境变量引用解析

## SSH 安全

### SSH 密钥

- 使用 SSH 密钥而非密码进行认证
- 密钥文件权限必须是 600 或更严格
- 支持 `${ENV_VAR}` 格式的密钥路径引用

### 远程命令执行

- 所有远程命令通过 SSH 加密通道传输
- 支持命令白名单验证（可选）
- 敏感操作需要用户确认

## 安全最佳实践

### 1. 环境隔离

```bash
# 生产环境使用独立凭据
export ASCEND_ENV=production
```

### 2. 日志脱敏

```python
# 日志中自动过滤敏感字段
sensitive_fields = ["password", "api_key", "token", "secret"]
```

### 3. 传输安全

- 所有网络通信使用加密连接
- MCP 服务器通信支持 TLS
- SSH 连接使用现代加密算法

### 4. 访问控制

- 配置文件读取权限检查
- 敏感操作需要二次确认
- 会话超时自动终止

## 漏洞报告

如果您发现安全漏洞，请通过以下方式报告：

1. **GitHub Issues**: 提交安全相关 Issue（请勿在公开评论中包含敏感信息）
2. **邮件联系**: 直接联系项目维护者

我们承诺：
- 24 小时内确认收到报告
- 7 天内提供初步修复计划
- 修复完成后公开致谢

## 安全更新

安全更新将独立于常规版本发布，并通过以下渠道通知：

- GitHub Security Advisories
- 项目 Release Notes

## 合规性

Ascend Op Agent 设计时遵循以下安全原则：

- **最小权限原则**: 只请求完成任务所需的最小权限
- **纵深防御**: 多层安全防护
- **默认安全**: 默认配置即为安全配置
- **透明可控**: 用户完全控制数据流和凭据
