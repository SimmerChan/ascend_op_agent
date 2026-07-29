# Skill 格式说明

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

## SKILL.md 结构

Skill 以 `SKILL.md` 文件 + YAML frontmatter 描述，由 `skills/storage.py` 与 `orchestrator/cannbot_loader.py` 解析：

```markdown
---
name: ascendc-elementwise
description: 元素级算子开发模板
tags: [ascendc, elementwise, template]
task_type: develop
topic: kernel_pattern
---

# AscendC Elementwise 算子开发模板

（正文：使用场景、API 约束、参考代码等）
```

### 命名规范（self-built skill）

- `name`：kebab-case，正则 `^[a-z][a-z0-9-]*[a-z0-9]$`，不含 PR 编号 / 日期 / task 对象名
- `task_type`：`migrate` / `analyze` / `optimize` / `develop` 之一（与 `task_store` 任务类型对齐）
- `topic`：阶段归类（如 `kernel_pattern` / `build_env` / `precision`），供 PR-B R5b 路由过滤

## 两类 Skill 来源

| 来源 | 加载器 | 存储位置 | 说明 |
|------|--------|---------|------|
| **cannbot-skills**（华为官方） | `orchestrator/cannbot_loader.py` | `vendor/cannbot-skills/` (submodule) | 消费官方 skill 仓库（cuda2ascend-simt / triton 5-skill 链等），按 `SKILL_BUNDLES` 决策表映射到编排器各阶段 |
| **self-built**（自研经验） | `skills/storage.py` + `skills/index.py` | `~/.ascend_op_agent/skills/self-built/{name}/SKILL.md` | 用户/Agent 固化的算子开发经验 |

### cannbot-skills 编排映射

`SKILL_BUNDLES`（`cannbot_loader.py`）按 `(graph, phase)` 查表加载 skill bundle，渲染成 PromptBuilder Layer 6 文本：

| Graph | Phase | Skill Bundle |
|-------|-------|-------------|
| migration | cuda_frontend | `cuda2ascend-simt` |
| migration | triton_frontend | triton 5-skill 链（task-extractor / op-designer / op-coding / op-verifier / latency-optimizer） |
| new_dev | design / analyze | `ascendc-tiling-design` + `ascendc-simt-tiling-design` + `npu-arch` |
| new_dev | codegen | `ascendc-direct-invoke-template` + `ascendc-simt-best-practices` |
| new_dev | review | `ascendc-code-review` |
| any | compile_fix | `ascendc-crash-debug` + `ascendc-runtime-debug` |
| any | precision_fix | `ascendc-precision-debug` + `pypto-precision-compare` |

## 存储结构

```
~/.ascend_op_agent/skills/
├── {name}/                    # 基础模板
│   └── SKILL.md
├── {name}_bugfix/             # Bugfix 经验
│   └── SKILL.md
├── {name}_performance/        # 性能优化技巧
│   └── SKILL.md
├── {name}_reference/          # 参考附件（flat dir）
└── self-built/
    └── {name}/                # 自研经验（Agent 可扫描）
        └── SKILL.md
```

## 检索：混合检索（FTS5 + 向量）

`skills/index.py` 的 `SkillsIndex` 提供 `hybrid_search(query, alpha, task_type, topic)`，组合两种检索：

- **SQLite FTS5 全文检索**（主索引）：虚拟表 schema 含 `name` / `description` / `tags` / `content` / `task_type` / `topic` 六列（PR-B 加 task_type + topic），tokenize=`porter unicode61`
- **向量检索**（ChromaDB）：embedding 模型可用时按语义相似度召回；模型加载失败时自动降级为纯 FTS5

`alpha` 控制 FTS5 权重（0-1），向量权重为 `1-alpha`。`task_type` / `topic` 非空时 FTS5 WHERE 子句过滤（PR-B R5b 路由）。

## Skill 结晶化（/learn）

`/learn <source-description>` 命令（`cli.py`）将算子开发经验自动固化为 self-built skill：

1. Agent 读源描述（目录 / 代码 / 文本）
2. 按 `_AUTHORING_STANDARDS`（kebab-case name + task_type + topic）生成 SKILL.md
3. 调 `skill_manage(action="create")` 工具写入 `self-built/{name}/SKILL.md`
4. 下次会话 Layer 6 自动渲染该 skill（按 task_type 路由）

PR-A（/learn + skill_manage 工具）与 PR-B（R5b 路由 + R6 hybrid + R7 分组 + R3 self-check）已落地。`skills/usage_tracker.py` 提供 Curator Lite 活跃度埋点。
