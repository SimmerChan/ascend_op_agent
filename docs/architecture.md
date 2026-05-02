# 架构设计

## 系统架构

```
CLI层
  └── ascend-op-agent

Agent核心层
  ├── AIAgent - 会话管理
  ├── PromptBuilder - 7层Prompt组装
  ├── ToolRegistry - 工具注册
  ├── ContextEngine - 上下文管理（含混合检索）
  └── MemorySystem - 四层记忆系统

工作流层
  └── OperatorWorkflow (Phase0-5 + Phase7 + Phase8)

集成层
  ├── SSHManager - SSH连接和文件同步
  ├── MCPClient - MCP服务器连接
  └── SkillRepository - 技能仓库管理

安全层
  ├── CredentialManager - SSH凭据管理
  └── TokenResolver - Token解析
```

## 记忆系统（四层）

1. **Working Memory** - 当前会话上下文（MemoryStore）
2. **Episodic Memory** - 完整会话历史（ChromaDB）
3. **Semantic Memory** - Skill知识向量（SkillIndex + ChromaDB）
4. **Procedural Memory** - 场景化工作流模板

## 7层Prompt结构

1. Agent Identity (SOUL.md)
2. Hermes Help Guidance
3. Tool-aware Behavioral Guidance
4. Custom System Message
5. Persistent Memory (SQLite)
6. Skills Index
7. Context Files