# Skill 安装功能补全

## Overview

补全 `ascend-op-agent skill install` 命令，实现从远程 Git 仓库获取并安装 Skill 的完整流程。

## Problem Frame

用户通过 `ascend-op-agent skill install <repo_url>` 安装远程 Skill 时，命令只有 TODO 注释而无实际功能。需要实现：远程仓库获取 → 交互式选择 → 本地安装 → 索引重建的完整流程。

## Requirements Trace

- R1. 用户可通过 `skill install <repo_url>` 从远程仓库获取可用 Skill 列表
- R2. 支持交互式多选（复用现有 `InteractiveSelector`）
- R3. 选中的 Skill 复制到本地 `~/.ascend_op_agent/skills/` 目录
- R4. 安装后自动重建索引
- R5. 安装命令支持已配置的 `skill_repositories` 中的仓库（无需每次输入 URL）

## Scope Boundaries

- 不涉及 Skill 市场或社区平台
- 不涉及 Skill 的更新/版本管理
- 不涉及远程 Skill 的删除或卸载

## Context & Research

### Relevant Code and Patterns

| 文件 | 用途 |
|-----|-----|
| `src/ascend_op_agent/cli.py:209-218` | `skill install` 命令，现有 TODO 占位 |
| `src/ascend_op_agent/skills/repository.py:304` | `SkillRepositoryDiscovery.fetch_skill_list()` — 已实现 |
| `src/ascend_op_agent/skills/interactive.py` | `InteractiveSelector` — 已实现，支持 TUI/文本模式 |
| `src/ascend_op_agent/skills/storage.py:253` | `SkillStorage.copy_skill()` — 已有复制逻辑 |
| `src/ascend_op_agent/skills/index.py:445` | `SkillIndex.rebuild_index()` — 重建索引 |

### Institutional Learnings

- 参考 Hermens Agent 的 Skills Hub 实现（无中心化市场，纯 Git 工作流）
- 本项目采用"本地优先 + 远程同步"架构

## Key Technical Decisions

- **复用 `InteractiveSelector`**：避免重复造轮子，支持 TUI 和纯文本回退
- **直接复制文件**：使用 `shutil.copytree` 从缓存目录复制到本地 skills 目录（覆盖式），而非解析再重建
- **延迟重建索引**：安装完成后统一重建，避免多次 IO
- **优先使用已配置仓库**：`install` 不带 URL 时，从 `config.skill_repositories` 选择

## Open Questions

### Resolved During Planning

- Q: 是否需要验证远程仓库格式？A: 由 `fetch_skill_list()` 内部处理，失败时提示用户
- Q: 已安装的 Skill 是否覆盖？A: 是，覆盖式安装
- Q: 单次安装多个还是逐个？A: 批量安装，统一重建索引

### Deferred to Implementation

- **远程仓库克隆失败的重试策略**：简单实现（最多重试 3 次，间隔 1s），复杂场景（认证失败、仓库不存在）直接报错
- 安装进度显示（可选优化）

## Implementation Units

- [x] **Unit 1: 补全 CLI install 命令**

**Goal:** 实现 `skill install` 命令主体逻辑

**Requirements:** R1, R2, R5

**Files:**
- Modify: `src/ascend_op_agent/cli.py`
- Test: `tests/test_cli.py`

**Approach:**
1. 解析 `repo_url` 参数：
   - 有 URL → 直接用 `SkillRepositoryDiscovery.fetch_skill_list(repo_url)` 获取列表
   - 无 URL → 检查 `config.skill_repositories` 是否为空：
     - 非空 → 提示用户选择仓库（或输入新 URL），可用 `InteractiveSelector` 复用已配置仓库列表
     - 为空 → 报错退出，提示用户先运行 `ascend-op-agent skill add <repo_url>` 添加仓库
2. 调用 `InteractiveSelector` 进行交互式多选（复用 R2）
3. 调用 `SkillInstaller.install_skills()` 安装

**Patterns to follow:**
- 参考 `skill add` 和 `skill remove` 的参数处理模式
- 参考 `InteractiveSelector` 的调用方式
- 参考 `InteractiveSelector` 复用已配置仓库列表进行选择的模式

**Test scenarios:**
- `install <url>` 直接从指定仓库获取列表并进入交互选择
- `install` 无 URL 且 `skill_repositories` 非空 → 提示选择已配置仓库
- `install` 无 URL 且 `skill_repositories` 为空 → 报错提示先 add
- 网络失败时给出友好提示
- 用户按 ESC 取消交互选择 → 安静退出

**Verification:**
- `python -m ascend_op_agent.cli skill install <url>` 调用 `fetch_skill_list()` 后进入交互选择界面
- `python -m ascend_op_agent.cli skill install` 无 URL 且仓库为空时报错
- InteractiveSelector 在有可选 skill 时正常显示选择列表

---

- [x] **Unit 2: 实现 Skill 安装逻辑**

**Goal:** 将远程 Skill 复制到本地并重建索引

**Requirements:** R2, R3, R4

**Files:**
- Create: `src/ascend_op_agent/skills/installer.py`
- Test: `tests/test_skill_install.py`（新建）

**Approach:**
1. 接收 `SkillInfo` 列表、缓存目录根路径和 `SkillIndex` 实例
2. 对每个选中的 Skill：
   - 用 `shutil.copytree` 从 `SkillInfo.path`（缓存内路径）复制到 `storage.skills_dir`
   - 覆盖已存在目录（`dirs_exist_ok=True`）并记录警告
3. 安装完成后，用缓存目录创建 `SkillRepository` 并调用 `rebuild_index(repo)`

**Technical design:**
```python
class SkillInstaller:
    def __init__(self, storage: SkillStorage):
        self.storage = storage

    def install_skills(
        self,
        skills: list[SkillInfo],
        repo_path: Path,  # 克隆后的缓存目录
        index: SkillIndex,
    ) -> dict[str, bool]:
        """安装选中的 skills，返回安装结果字典 {skill_name: success}"""
        results = {}
        for skill_info in skills:
            src_path = Path(skill_info.path)
            dest_name = src_path.name
            dest_path = self.storage.skills_dir / dest_name

            # 覆盖安装前警告
            if dest_path.exists():
                logger.warning(f"覆盖已有 skill: {dest_name}")

            try:
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                results[dest_name] = True
            except Exception as e:
                logger.error(f"安装失败 {dest_name}: {e}")
                results[dest_name] = False

        # 重建索引（使用缓存目录创建 SkillRepository）
        if any(results.values()):
            repo = SkillRepository(local_skills_dir=repo_path)
            index.rebuild_index(repo)

        return results
```

**路径映射说明：**
- `SkillInfo.path` 指向克隆缓存目录内的 skill 目录（如 `/tmp/.ascend_op_agent/.skill_cache/repo_name/op_type_name/`）
- `repo_path` = `clone_or_update()` 返回的根缓存目录
- `SkillInfo.path` = `repo_path / skill_dir_name`
- 复制源：`Path(skill_info.path)`，复制目标：`storage.skills_dir / dest_name`

**Patterns to follow:**
- `SkillRepositoryDiscovery.clone_or_update()` 的缓存管理
- `shutil.copytree(..., dirs_exist_ok=True)` 覆盖式复制

**Test scenarios:**
- 安装单个 Skill 成功
- 安装多个 Skills 成功（部分成功部分失败时返回混合结果）
- 目标目录已存在时覆盖安装（带日志警告）
- 克隆缓存路径无效时记录错误并跳过

**Verification:**
- 调用 `install_skills()` 后，本地 `~/.ascend_op_agent/skills/` 目录包含对应的 skill 目录
- `install_skills()` 返回 `dict[str, bool]` — 每个 skill 的成功/失败状态清晰可查
- 索引重建后可以搜索到新安装的 Skill

---

- [x] **Unit 3: 集成测试**

**Goal:** 验证完整安装流程

**Requirements:** R1-R5

**Files:**
- Create: `tests/integration/test_skill_install.py`
- Modify: `tests/test_cli.py`（新增 install 相关用例）

**Approach:**
1. 使用临时目录模拟远程仓库
2. 创建测试用 SKILL.md 文件
3. 验证完整流程：fetch → select → install → search

**Test scenarios:**
- 端到端安装流程测试
- 交互式选择取消时的处理
- 空仓库的处理

**Verification:**
- `pytest tests/integration/test_skill_install.py` 通过
- `pytest tests/test_cli.py -k skill` 通过

## System-Wide Impact

- **Interaction graph:** CLI → `SkillInstaller` → `SkillStorage` + `SkillRepositoryDiscovery` → `SkillIndex`
- **Error propagation:** 网络错误由 `fetch_skill_list()` 抛出，在 CLI 层捕获并提示
- **State lifecycle risks:** 覆盖安装可能导致用户修改丢失（已有警告）
- **Integration coverage:** 涉及 CLI、Storage、Repository、Index 四个模块的集成

## Risks & Dependencies

- **风险**: 远程仓库克隆失败（网络或 URL 错误）
  - **缓解**: 异常捕获，友好提示，不阻塞 CLI
- **风险**: 覆盖安装丢失用户修改
  - **缓解**: 安装前通过 `logger.warning()` 输出覆盖警告；未来增加备份逻辑
- **依赖**: `fetch_skill_list()` 和 `InteractiveSelector` 已稳定

## Documentation / Operational Notes

- 更新 CLI 帮助文本中的 `install` 示例
- 建议用户在安装新 Skill 前备份本地修改
