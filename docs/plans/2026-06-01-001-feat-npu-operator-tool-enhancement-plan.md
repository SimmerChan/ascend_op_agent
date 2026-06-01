---
title: "NPU Operator Tool System Enhancement"
type: feat
status: completed
date: 2026-06-01
origin: docs/brainstorms/2026-06-01-npu-operator-tool-requirements.md
---

# NPU Operator Tool System Enhancement

## Summary

Expand the agent's tool system to support the full昇腾 NPU operator development lifecycle. Phase 1 already migrated `file_read`, `file_write`, `file_search`, `patch`, and `shell_exec` from Hermes Agent. This plan covers Phase 2-5: adding `python_exec`, `git_*` tools, and `npu_*` hardware tools.

---

## Problem Frame

The current tool system (Phase 1) handles basic file and shell operations but lacks tools critical for NPU operator development: Python script execution for CANN API testing, Git operations for version control, and NPU hardware introspection tools. Without these, the agent cannot support the full operator development workflow from implementation through hardware deployment.

---

## Requirements

- R1. Agent can execute Python scripts in isolation (subprocess, timeout, result capture)
- R2. Agent can query Git history, diff, status, and branches within a repo
- R3. Agent can query NPU device info via `npu-smi` or equivalent
- R4. Agent can run CANN toolchain commands (`msop`, compilation)
- R5. All new tools follow the existing self-registration pattern in `agent/tools/`
- R6. All tools return structured results (`{"success": bool, "result"/"error": str}`)
- R7. Tool results are recorded in session history as `ToolEntry` records

---

## Scope Boundaries

- **In scope:** `python_exec`, `git_log`, `git_diff`, `git_status`, `git_branch`, `npu_smi`, `msop`, `cann_compile`
- **Deferred:** Browser automation, delegate/sub-agent tools, Kanban coordination tools
- **Outside scope:** Multi-agent orchestration, gateway/platform integrations, custom model providers

---

## Context & Research

### Relevant Code and Patterns

- `src/ascend_op_agent/agent/tools/__init__.py` — existing tool self-registration entry point
- `src/ascend_op_agent/agent/tools/shell_tool.py` — reference for tool schema format and `register()` pattern
- `src/ascend_op_agent/agent/tools/file_search_tool.py` — reference for multi-mode tools (content/files)
- `src/ascend_op_agent/agent/tool_registry.py` — `ToolRegistry.register()` and `call_tool()` interface
- `src/ascend_op_agent/agent/core.py` — tool invocation via `tool_registry.call_tool()`, result recording as `ToolEntry`
- Hermes Agent `tools/code_execution_tool.py` — reference for `python_exec` implementation
- Hermes Agent `tools/terminal_tool.py` — reference for multi-environment shell execution

### Institutional Learnings

- Phase 1 tool migration demonstrated the `lambda **kw: func(**kw)` wrapper pattern works cleanly with `Tool.execute(**kwargs)`
- `SCHEMA` dict + `register(registry)` pattern provides clean separation between definition and registration
- `tool_registry.call_tool()` already returns the correct `{"success", "result"/"error"}` structure used by `ToolEntry`

---

## Key Technical Decisions

- **`python_exec` uses subprocess isolation**: No `eval()` or import in-agent process. Subprocess with timeout and output capture. Rationale: safety — operator development involves untrusted CANN script code.
- **`git_*` tools use subprocess git commands**: `git log`, `git diff`, `git status`, `git branch` all invoke `subprocess.run(["git", ...])`. Rationale: Hermes uses the same pattern; avoids re-implementing Git parsing.
- **`npu_*` tools use CLI wrappers**: `npu-smi` is a Huawei-provided CLI; `msop` is CANN's operator analysis tool. Both are invoked via `shell_exec`-equivalent wrappers. Rationale: these are vendor-provided tools, not internal APIs.
- **New tools live in `agent/tools/`**: `python_exec_tool.py`, `git_tool.py`, `npu_tool.py`. Modular separation by category.

---

## Open Questions

### Resolved During Planning

- **Q: Can we reuse Hermes `code_execution_tool.py` directly?** A: No — it depends on Hermes-specific context objects (`task_id`, `TERMINAL_CWD`). We'll implement `python_exec` using the same subprocess pattern but with Ascend-specific environment handling.
- **Q: Should `npu_smi` be a standalone tool or a mode of `shell_exec`?** A: Standalone — it has distinct schema (device ID, memory info, utilization) and should appear separately in tool listings.

### Deferred to Implementation

- Whether to implement `git_branch` create/delete or read-only only (取决于开发流程需求)
- Exact schema for `npu_smi` output (depends on Huawei CANN version installed)
- Whether CANN compilers (`cann_compile`) require special environment variables or activation

---

## Implementation Units

- U1. **[Python Exec Tool]**

**Goal:** Execute Python scripts in isolated subprocess with timeout and output capture

**Requirements:** R1, R5, R6, R7

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/agent/tools/python_exec_tool.py`
- Modify: `src/ascend_op_agent/agent/tools/__init__.py`

**Approach:**
- Use `subprocess.Popen` with `stdout/stderr` capture and timeout via `wait(timeout)`
- Support `python_exec(code: str, timeout: int = 30, cwd: str = None)`
- Return formatted stdout/stderr or error message

**Patterns to follow:**
- `shell_tool.py` for schema structure and `register()` pattern
- Hermes `code_execution_tool.py` for subprocess isolation approach

**Test scenarios:**
- Happy path: Execute `print("hello")` → returns "hello"
- Happy path: Execute with custom `cwd` → respects working directory
- Edge case: Script with syntax error → returns error message
- Edge case: Script exceeding timeout → returns timeout error
- Error path: Invalid Python → returns `SyntaxError` or `NameError`

**Verification:**
- `tool_registry.call_tool("python_exec", {"code": "print(1+1)"})` returns `{"success": true, "result": "2"}`

---

- U2. **[Git Log Tool]**

**Goal:** Query Git commit history for a repository

**Requirements:** R2, R5, R6, R7

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/agent/tools/git_tool.py` (bundles all git tools)

**Approach:**
- Single file `git_tool.py` with `git_log`, `git_diff`, `git_status`, `git_branch` as module-level functions
- Each tool is a thin wrapper around `subprocess.run(["git", <subcommand>, ...])`
- Unified `register()` function registers all git tools at once

**Patterns to follow:**
- `shell_tool.py` subprocess pattern
- Hermes git tools (if any) for output formatting

**Test scenarios:**
- Happy path: `git_log` on a real repo → returns formatted commit list
- Happy path: `git_status` → returns clean or dirty status
- Happy path: `git_diff` with changes → returns diff output
- Edge case: Not a git repo → returns error

**Verification:**
- `tool_registry.call_tool("git_log", {"path": "."})` returns commit history

---

- U3. **[Git Diff/Branch Tools]**

**Goal:** Complete Git operations: diff, branch listing

**Requirements:** R2, R5, R6, R7

**Dependencies:** U2 (same file)

**Approach:**
- `git_diff(path, ref1, ref2)` — compare two refs or show working tree diff
- `git_branch(list_branches=True)` — list all branches

**Test scenarios:**
- Happy path: `git_branch` → lists branches with current branch marked
- Happy path: `git_diff` between two commits → returns unified diff
- Edge case: No changes → returns empty diff

**Verification:**
- `git_branch` shows local branches; `git_diff` shows staged/unstaged changes

---

- U4. **[NPU SMI Tool]**

**Goal:** Query NPU device information

**Requirements:** R3, R5, R6, R7

**Dependencies:** None

**Files:**
- Create: `src/ascend_op_agent/agent/tools/npu_tool.py`

**Approach:**
- `npu_smi()` queries `npu-smi` CLI if available, falls back to error if not installed
- Output is parsed and formatted as readable text
- Schema: `npu_smi(device_id: str = None)` — optional device filter

**Patterns to follow:**
- `shell_tool.py` for CLI invocation pattern

**Test scenarios:**
- Happy path: NPU available → returns device info (ID, memory, utilization)
- Edge case: No NPU hardware → returns error message
- Edge case: Specific device ID queried → returns that device only

**Verification:**
- `tool_registry.call_tool("npu_smi", {})` returns NPU device info

---

- U5. **[MSOP Tool]**

**Goal:** Run CANN `msop` operator analysis tool

**Requirements:** R4, R5, R6, R7

**Dependencies:** U4

**Approach:**
- `msop(operator_path: str, analyze: bool = True)` — run msop on an operator file or directory
- `cann_compile(operator_path: str, target: str = "npu")` — wrap CANN compilation command

**Test scenarios:**
- Happy path: `msop` on valid operator → returns analysis output
- Happy path: `cann_compile` → returns compilation result
- Edge case: `msop` not found → returns error with installation hint

**Verification:**
- `tool_registry.call_tool("msop", {"operator_path": "/path/to/op"})` returns analysis

---

## System-Wide Impact

- **Tool registry growth:** New tools are added to `tool_registry._tools` dict on import — no other component changes
- **LLM schema generation:** `tool_registry.to_openai_format()` returns all tools including new ones — no changes needed
- **Session recording:** `ToolEntry` recording already handles `success`, `result`, `error` fields — no changes needed
- **No API surface changes:** All tool interfaces are internal to the agent

---

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| CANN toolchain not installed on target machine | Graceful error with install hint |
| `npu-smi` not in PATH | Fall back to environment variable detection or error |
| Python subprocess security | Subprocess isolation, no `eval()`, timeout enforced |
| Git repo state corruption | Git commands are read-only by default; warn on destructive operations |

---

## Documentation / Operational Notes

- Add tools to `CLAUDE.md` section "常用命令" under `ascend-op-agent` tool list
- Document required environment: Python 3.x, Git, CANN SDK (for NPU tools)

---

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-01-npu-operator-tool-requirements.md](../brainstorms/2026-06-01-npu-operator-tool-requirements.md)
- Hermes Agent tool architecture: `/Users/huangshilei/Documents/pythonprojects/hermes-agent/tools/`
- Phase 1 implementation: `src/ascend_op_agent/agent/tools/`