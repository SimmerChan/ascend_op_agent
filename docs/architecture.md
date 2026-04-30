# 架构设计

## 系统架构

```
CLI层
  └── ascend-op-agent

Agent核心层
  ├── AIAgent - 会话管理
  ├── PromptBuilder - 7层Prompt组装
  ├── ToolRegistry - 工具注册
  ├── ContextEngine - 上下文管理
  └── MemoryStore - 持久化记忆

工作流层
  └── OperatorWorkflow (Phase0-5)

集成层
  ├── SSHManager - SSH连接和文件同步
  ├── MCPClient - MCP服务器连接
  └── SkillRepository - 技能仓库管理

安全层
  ├── CredentialManager - SSH凭据管理
  └── TokenResolver - Token解析
```

## 7层Prompt结构

1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory (SQLite)
6. Skills Index
7. Context Files