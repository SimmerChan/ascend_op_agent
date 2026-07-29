# Skill 知识库

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

## 概述

Skill 知识库由两层构成：

1. **cannbot-skills 知识层**（`orchestrator/cannbot_loader.py`）-- 消费华为官方
   `vendor/cannbot-skills` submodule，按 `(graph, phase)` 决策表加载 skill bundle 注入
   PromptBuilder Layer 6。
2. **自研 self-built skill 层**（`skills/` + `agent/tools/skill_manage_tool.py`）-- 用户/agent
   通过 `/learn` 命令固化开发经验为 self-built skill，支持 FTS5 + 向量混合检索。

## 架构

```mermaid
graph TB
    subgraph Cannbot["cannbot 知识层 orchestrator/cannbot_loader.py"]
        A[SKILL_BUNDLES 决策表]
        B[build_skill_bundle]
        C[render_skill_bundle_text]
        D[SkillUsageRegistry signal-1 跟踪]
    end

    subgraph SelfBuilt["self-built skill 层 skills/"]
        E[SkillRepository]
        F[SkillIndex FTS5+向量]
        G[SkillStorage]
        H[SkillInstaller]
        I[UsageTracker curator-lite]
    end

    subgraph Tools["agent 工具"]
        J[skill_manage_tool 7 actions]
        K[/learn 命令 cli.py]
    end

    subgraph Storage["存储"]
        L["~/.ascend_op_agent/skills/"]
        M["vendor/cannbot-skills/"]
    end

    A --> M
    B --> C
    K --> J
    J --> G
    J --> I
    E --> L
    F --> E
```

## cannbot-skills 知识层

`orchestrator/cannbot_loader.py` 解析 cannbot SKILL.md（YAML frontmatter + body +
`@references/*.md` 相对引用），按决策表构建 skill bundle。

### SKILL_BUNDLES 决策表

`(graph, phase) -> [skill 相对路径]` 映射：

| graph | phase | skills |
|-------|-------|--------|
| `new_dev` | `design` | ascendc-tiling-design, ascendc-simt-tiling-design, npu-arch |
| `new_dev` | `codegen` | ascendc-direct-invoke-template, ascendc-simt-best-practices |
| `new_dev` | `review` | ascendc-code-review |
| `migration` | `cuda_frontend` | cuda2ascend-simt |
| `migration` | `triton_frontend` | triton-op-coding, triton-op-designer, triton-op-verifier, triton-latency-optimizer, triton-task-extractor |
| `any` | `compile_fix` | ascendc-crash-debug, ascendc-runtime-debug |
| `any` | `precision_fix` | ascendc-precision-debug, pypto-precision-compare |

### 核心函数

```python
from ascend_op_agent.orchestrator import (
    build_skill_bundle, render_skill_bundle_text, CannbotSkill, SKILL_BUNDLES,
    SkillUsageRegistry, list_cannbot_skill_names,
)

# 按 (graph, phase) 加载 skill bundle
skills = build_skill_bundle(phase="codegen", graph="new_dev")
# 渲染为 Layer 6 文本（codegen 阶段可内联 add_example 构建参考工程）
text = render_skill_bundle_text(skills, phase="codegen", inline_build_template=True)

# SkillUsageRegistry 单例：记录每阶段加载/使用的 skill 名（signal-1 跟踪）
SkillUsageRegistry.instance().record_load("t1", "codegen", [s.name for s in skills])
```

`CANBOT_BUNDLE_MAP` 是 `(graph, phase) -> (task_type, topic)` 1:1 映射，供 R5b 路由把
PhaseRunner 的 phase 映射到 task_type/topic 过滤 self-built skill。

## SKILL.md 格式

```markdown
---
name: ascendc-elementwise
description: AscendC 元素级算子开发模板
metadata:
  hermes:
    tags: [ascendc, elementwise, template]
version: 1.0.0
author: SimmerChan
---

# AscendC Elementwise 算子开发模板

## 适用场景
...
```

## 目录结构

```
~/.ascend_op_agent/skills/
├── self-built/                    # 自研 skill（/learn 固化）
│   └── {name}/
│       └── SKILL.md
├── {name}_bugfix/                 # Bugfix 维度
│   └── SKILL.md
├── {name}_performance/            # 性能优化维度
│   └── SKILL.md
├── {name}_reference/              # 参考文档（R13）
│   └── *.md
└── .usage.json                    # curator-lite 活跃度汇总表

vendor/cannbot-skills/             # 华为官方 skill submodule（只读）
└── ops/                           # 算子开发 skill
    ├── ascendc-direct-invoke-template/
    ├── cuda2ascend-simt/
    └── ...
```

## 核心组件

### SkillRepository

本地 skill 目录扫描与加载：

```python
from ascend_op_agent.skills.repository import SkillRepository, SkillRepositoryDiscovery

repo = SkillRepository(local_skills_dir="~/.ascend_op_agent/skills")
skills = repo.list_local_skills()        # -> list[SkillInfo]
skill = repo.get_skill("ascendc-elementwise")  # -> Skill | None
```

`SkillRepositoryDiscovery` 支持从远程 Git 仓库克隆/更新并扫描 skill 列表。

### SkillIndex

SQLite FTS5 全文检索 + 向量相似度混合检索（PR-B v2 schema 含 `task_type` / `topic` 列）：

```python
from ascend_op_agent.skills.index import SkillIndex

index = SkillIndex()
index.add_skill(skill, task_type="develop", topic="kernel_pattern")

# 混合检索（FTS5 + 向量，alpha 控制 FTS5 权重）
results = index.hybrid_search("AscendC elementwise", k=5, alpha=0.7,
                               task_type="develop", topic="kernel_pattern")

# 单路检索
results = index.search("elementwise", k=5)              # FTS5
results = index.search_by_vector(query_embedding, k=5)  # 向量
results = index.search_by_task_type("develop")          # task_type 过滤
```

两层缓存：进程内 LRU（`OrderedDict`，cap 8）+ 磁盘快照（`.skills_prompt_snapshot.json`）。
embedding 模型惰性加载（加载失败时向量功能降级，FTS5 保留）。

### SkillStorage

本地文件系统存储，支持混合维度组织：

```python
from ascend_op_agent.skills.storage import SkillStorage

storage = SkillStorage(skills_dir="~/.ascend_op_agent/skills")
path = storage.save_skill(skill, dimension="self_built")  # template/bugfix/performance/self_built/reference
skill = storage.load_skill("my-skill")
```

### SkillInstaller

从远程仓库克隆后将 skill 复制到本地并重建索引：

```python
from ascend_op_agent.skills.installer import SkillInstaller

installer = SkillInstaller()
results = installer.install_skills(skills, repo_path, index)  # -> {skill_name: success}
```

## Skill Crystallization（/learn）

`/learn` 命令（`cli.py`）触发经验固化，调用 `skill_manage` 工具创建 self-built skill：

```
/learn the build.sh ASCEND_COMPUTE_UNIT fix we just made
/learn /home/hsl/ops_agent/build_configs
/learn 910B3 set_env.sh must be sourced before msopgen
```

### skill_manage 工具（7 actions）

`agent/tools/skill_manage_tool.py` 提供 self-built skill 全生命周期管理：

| action | 说明 |
|--------|------|
| `create` | 校验（R2）+ 保存新 skill 到 `self-built/{name}/SKILL.md` |
| `patch` | 全量替换保存（PR-A 不做 diff/merge） |
| `add_reference` | 给已有 skill 附加参考文档（R13） |
| `archive` | 移到 `.archived/` + 注入 `archive_at`/`archive_reason` |
| `load` | 读取 SKILL.md + 解析 frontmatter |
| `list_skills` | 枚举 self-built skill |
| `search` | frontmatter 扫描过滤（FTS5 集成在 PR-B） |

```python
# skill_manage 返回结构化结果（R16 SkillManageError）
{"success": True, "name": "my-skill", "path": "..."}
{"success": False, "error": "INVALID_NAME", "field": "name",
 "reason": "...", "remediation_hint": "..."}
```

## Skill Curator Lite（Tier 0）

`skills/usage_tracker.py` 的 `UsageTracker` 记录 self-built skill 活跃度计数，
作为 STRATEGY.md「Skill reuse rate」metric 的 numerator：

```python
from ascend_op_agent.skills.usage_tracker import UsageTracker

tracker = UsageTracker()  # ~/.ascend_op_agent/skills/.usage.json
# skill_manage(action="load") 埋点：use_count / last_used_at
# skill_manage(action="patch") 埋点：patch_count / last_activity_at
```

特性：原子写（tempfile + os.replace）；缺失/损坏/形状错一律返空 + warn，永不抛
（best-effort 埋点不阻塞主操作）。只追踪 self-built skill（cannbot 走独立 CANNBOT_ROOT）。

## 检索策略

### 混合检索

`SkillIndex.hybrid_search` 结合 FTS5 关键词匹配与向量相似度：

```python
results = index.hybrid_search(
    query="AscendC elementwise",
    k=5,
    alpha=0.7,           # FTS5 权重，向量权重 = 1 - alpha
    task_type="develop", # 可选 task_type 过滤（PR-B）
    topic="kernel_pattern",  # 可选 topic 过滤（PR-B）
)
```

embedding 模型不可用时自动回退到纯 FTS5。

### 向量检索

使用 sentence-transformers 生成嵌入向量（默认 `all-MiniLM-L6-v3`，384 维），
持久化到 `VectorStore`（ChromaDB）。

## 生命周期

```mermaid
graph LR
    A[/learn 触发] --> B[skill_manage create]
    B --> C[SkillStorage 写盘]
    C --> D[SkillIndex 索引]
    D --> E[hybrid_search 检索]
    E --> F[agent 复用]
    F --> G[UsageTracker 埋点]
    G --> H[patch 更新]
    H --> D
```
