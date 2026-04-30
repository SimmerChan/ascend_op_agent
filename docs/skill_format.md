# Skill格式说明

## SKILL.md 结构

```markdown
---
name: ascendc-elementwise
description: 元素级算子开发模板
tags: [ascendc, elementwise, template]
version: 1.0.0
---

# AscendC Elementwise 算子开发模板
```

## Skill维度

1. **Template**: 基础算子开发模板
2. **Bugfix**: 编译错误修复经验
3. **Performance**: 性能优化技巧

## 存储结构

```
~/.ascend_op_agent/skills/
├── {backend}_{op_type}/
│   └── SKILL.md
├── {backend}_{op_type}_bugfix/
│   └── SKILL.md
└── {backend}_{op_type}_performance/
    └── SKILL.md
```

## 检索

使用SQLite FTS5全文检索