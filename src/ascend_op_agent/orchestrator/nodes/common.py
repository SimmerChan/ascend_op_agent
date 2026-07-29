"""make_llm_node —— LLM 节点工厂(实现 P0-2 共存契约)。

U5 修复:LLM 命名飘移防御。任何 LLM 节点的 response 都会被解析:

1. ``<<OP_INFO>>{json}<<END>>`` 结构化块 → 写 ``state["op_info"]``
   (analyze 阶段产出,后续 codegen/compile 用)
2. 路径以 ``op_host/`` ``op_kernel/`` ``op_graph/`` 开头的 markdown 提取
   文件,若 LLM 用了非 ``state.op_info.name`` 的 snake_case 前缀(如
   ``add_custom_def.cpp``),rename 到 ``{op_snake}_def.cpp``(防止
   CMakeLists 引用 ``op_add_def.cpp`` 但 LLM 写了 ``add_custom_def.cpp``
   → No rule to make target 编译失败)

每个 LLM 节点封装一个 **fresh AIAgent**,按以下顺序工作:

1. ``agent = agent_factory()`` —— 每节点新 AIAgent(``session_manager=None``,
   编排器 owns 持久化)
2. ``agent._conversation_history = list(state["messages"])`` —— 从 checkpoint
   rehydrate 对话历史(同形 ``list[{"role","content"}]``,零转换)
3. 从 ``state["memory_pools"]`` rehydrate MemoryStore(memory pool 跨节点持久化)
4. 构建 task_prompt(模板 + state 字段插值)
5. ``agent.run_conversation(task_prompt, skills_layer_override=skill_bundle_text)``
   —— scoped skill 文本注入 PromptBuilder Layer 6(P0-1 修正)
6. 返回 update dict:

   - ``messages``: append ``[{"role":"assistant","content":response}]``
   - ``memory_pools``: merge agent 端最新 memory 快照
   - ``last_phase_result``: ``{"phase":phase,"response":response}``

幂等契约(P0-3 修正):

- HITL resume(``state["pending_confirmation"]`` 非空):节点不重跑 LLM,直接返回
  ``{"pending_confirmation": None}`` 让编排器推进。这是 design / delivery_mode
  这类"等用户确认"节点的通用模式
- 工件级幂等(compile/precision 等长跑节点)由 ``CheckpointStore.has_artifact_with_sha``
  在节点实现内单独处理,不在此处兜底
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ascend_op_agent.orchestrator.state_machine import Node

AgentFactory = Callable[[], Any]

# LLM 命名飘移防御:识别 codegen 语义目录里的"其他 op 名"前缀。
# 这些是 LLM hallucinate 出的真实失败案例(2026-07-29 e2e 暴露:
# add_custom_def.cpp 残留 → CMakeLists 引 op_add_def.cpp 编译挂)。
# 命中后 rename 到 state.op_info.name。
_KNOWN_BAD_OP_PREFIXES = ("add_example", "add_custom", "AddExample", "AddCustom")


def to_pascal(snake: str) -> str:
    """snake_case -> PascalCase('vector_add' -> 'VectorAdd','add' -> 'Add')。

    U3 方向 B:op 名参数化 scaffold 时,从 snake_case op 名转 PascalCase 类名。
    """
    return "".join(part.capitalize() for part in snake.split("_") if part)


def parse_op_info_block(response: str) -> dict | None:
    """从 LLM response 抽 ``<<OP_INFO>>{json}<<END>>`` 结构化块。

    失败(无块/JSON 解析错)返 None,不抛。analyze 节点 prompt 末尾要求
    LLM 产此结构化块,后续 codegen/compile 节点从 ``state["op_info"]``
    取 op 名,避免下游 LLM 再 hallucinate 出 add_custom_ 等错误前缀。
    """
    import json
    import re

    m = re.search(r"<<OP_INFO>>\s*(\{.*?\})\s*<<END>>", response, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    class_name = obj.get("class_name")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(class_name, str) or not class_name:
        class_name = to_pascal(name)
    return {"name": name, "class_name": class_name}


def enforce_op_naming(files: list[dict], op_snake: str) -> tuple[list[dict], int]:
    """扫 ``code_result.files``,rename 语义目录里用了错前缀的文件。

    范围:``op_host/`` ``op_kernel/`` ``op_graph/`` 下的 cpp/h 文件。
    错前缀:已知 LLM 幻觉的 ``add_example_*`` / ``add_custom_*`` / ``AddExample_*``
    / ``AddCustom_*``(2026-07-29 e2e 实证残留)。``<op_snake>_*`` 与
    ``OpClass_*`` 视为正确(不动)。

    Args:
        files: ``[{"path": str, "content": str, ...}]``
        op_snake: ``state.op_info.name``(如 ``op_add``)

    Returns:
        (renamed_files, n_renamed) —— 重命名后的 files 列表 + rename 次数
        便于观测。
    """
    import re
    from pathlib import Path

    if not op_snake:
        return files, 0
    # 文件名 basename 匹配:add_custom_def -> add_custom,suffix 可选
    # (允许 add_custom.h 这种 stem 完全等于错前缀的情况)
    pattern = re.compile(
        r"^(?P<prefix>(" + "|".join(_KNOWN_BAD_OP_PREFIXES) + r"))(?P<suffix>[_.].+)?$"
    )
    renamed: list[dict] = []
    n = 0
    for f in files:
        path = f.get("path", "")
        # 仅语义目录
        if not any(seg in path.split("/") for seg in ("op_host", "op_kernel", "op_graph")):
            renamed.append(f)
            continue
        p = Path(path)
        stem = p.stem  # add_custom_def 或 add_custom
        suffix = p.suffix  # .cpp / .h
        m = pattern.match(stem)
        if not m:
            renamed.append(f)
            continue
        # 重命名: add_custom_def -> op_add_def,add_custom -> op_add
        kept_suffix = m.group("suffix") or ""
        new_stem = f"{op_snake}{kept_suffix}"
        new_name = new_stem + suffix
        new_path = str(p.with_name(new_name))
        # 内容里也同步替换(避免 content 引用 add_custom 不一致)
        new_content = f.get("content", "")
        for bad in _KNOWN_BAD_OP_PREFIXES:
            new_content = new_content.replace(bad, op_snake)
        renamed.append({**f, "path": new_path, "content": new_content})
        n += 1
    return renamed, n


def make_llm_node(
    phase: str,
    task_prompt_template: str,
    skill_bundle_text: Optional[str] = None,
    agent_factory: Optional[AgentFactory] = None,
    template_vars: Optional[dict] = None,
    skill_names: Optional[list[str]] = None,
    no_tools: bool = False,
) -> Node:
    """构造 LLM 节点。

    Args:
        phase: 阶段名(用作 current_phase / last_phase_result.phase)
        task_prompt_template: 任务 prompt 模板,支持 ``{state}`` ``{user_input}``
            ``{pending_confirmation}`` 占位符(简单 ``str.format``)。
            额外占位符由 ``template_vars`` 注入(任意 key=value,需为 string)。
        skill_bundle_text: 该阶段 cannbot skill 文本(注入 PromptBuilder Layer 6)。
            None 时使用 PromptBuilder 默认 Layer 6
        template_vars: 额外模板变量(供 ``str.format``),如
            ``{"operator_dir": "/tmp/e2e_ops_local/op_add"}``。
            None 时不注入。KeyError 仍触发 fallback(见 _node 内 try)。
        agent_factory: 返回 fresh AIAgent 的工厂(``agent_factory()``)。测试时
            替换为 mock。生产时是 ``lambda: AIAgent(config, ...,
            session_manager=None)``
        skill_names: 该阶段加载的 cannbot skill 名(供 U2 SkillUsageRegistry 跟踪)。
            None 时不记录 skill 使用。配合 skill_bundle_text 使用 —— 文本注入 LLM,
            名字记入 registry。

    Returns:
        Node —— PhaseRunner 直接消费
    """
    if agent_factory is None:
        raise ValueError("agent_factory is required (use lambda for production wiring)")

    def _node(state: dict) -> dict:
        # 1. HITL 恢复:幂等跳过 LLM 调用
        pending = state.get("pending_confirmation")
        if pending is not None:
            return {
                "pending_confirmation": None,
                "last_phase_result": {
                    "phase": phase,
                    "skipped_llm": True,
                    "pending": pending,
                },
            }

        # 2. 构造 fresh agent
        agent = agent_factory()

        # 3. rehydrate 对话历史(同形,零转换)
        agent._conversation_history = list(state.get("messages", []))

        # 4. rehydrate memory_pools
        existing_memory = state.get("memory_pools", {})
        for pool, items in existing_memory.items():
            if isinstance(items, list):
                for item in items:
                    agent.memory.add(pool, item)

        # 5. 构建 task prompt
        user_input = ""
        messages = state.get("messages", [])
        if messages:
            first_user = next((m for m in messages if m.get("role") == "user"), None)
            if first_user is not None:
                user_input = first_user.get("content", "")

        try:
            format_kwargs = {
                "state": state,
                "user_input": user_input,
                "pending_confirmation": pending,
            }
            if template_vars:
                format_kwargs.update(template_vars)
            task_prompt = task_prompt_template.format(**format_kwargs)
        except KeyError:
            # 模板里出现了不支持的占位符,降级为原样(LLM 看到字面 {x} 会瞎填)
            task_prompt = task_prompt_template

        # 6. 调用 agent(scoped skill 注入 Layer 6)
        # U1:把 task_type 从 state 透传给 agent —— PromptBuilder Layer 6 降级前置。
        # PhaseRunner.invoke(task_type=...) 写入 state["task_type"],这里读出
        # 传进 run_conversation,后者存到 self._current_task_type 供 build_system_prompt 用。
        response = agent.run_conversation(
            task_prompt,
            skills_layer_override=skill_bundle_text,
            task_type=state.get("task_type"),
            no_tools=no_tools,
        )

        # 7. 抓取 memory 快照(merge 现有)
        new_memory_pools: dict[str, list[str]] = dict(existing_memory)
        for pool in ("memory", "user"):
            items = agent.memory.get(pool)
            if items:
                new_memory_pools[pool] = list(items)

        # 8. 从 agent._tool_calls_log 抓 file_write 落盘的文件,合并到 code_result。
        # 解决 e2e 暴露的 Gap 1:_conversation_history 不存 tool args,
        # 编排器拿不到 file_write 实际路径(commit 1783f9b 记录)。
        code_result = dict(state.get("code_result") or {})
        existing_files = list(code_result.get("files") or [])
        # U1(强化 fix_loop):existing_paths 不预填 existing 已有 path —— 允许同 path
        # 覆盖(第 2 轮 fix 修同一文件必须生效)。仅用于 markdown 分支防同响应内重复。
        existing_paths = set()
        # U1:file_write 同 path 取后者(dict 保插入序),原列表推导 `not in existing_paths`
        # 会让第 2 轮新 content 进不了 code_result.files。
        _fw_by_path: dict[str, str] = {}
        for entry in getattr(agent, "_tool_calls_log", []):
            if entry.get("name") != "file_write":
                continue
            _fw_path = entry.get("args", {}).get("path")
            if not _fw_path:
                continue
            _fw_by_path[_fw_path] = entry["args"].get("content", "")
        new_files = [
            {"path": p, "content": c, "tool": "file_write"} for p, c in _fw_by_path.items()
        ]

        # 8b. Markdown fallback:某些 LLM(尤其 OpenAI 协议下的 GLM-5.2)在 codegen
        # 阶段不调 file_write tool(可能 tool schema 不识 / 调 shell_exec 循环失败
        # 达 max_iterations),但会输出 ```cpp // path/... \n<content>\n``` markdown
        # 代码块。提取这些代码块进 code_result(同 e2e_real_op._extract_files_from_messages
        # 的格式 1)。向后兼容:file_write 路径不变。
        if not new_files:
            # 扫 agent._conversation_history(LLM response 进这里,不是 state.messages;
            # state["messages"] 是 checkpoint 持久化层,本节点的 LLM response 还没 append)
            for m in getattr(agent, "_conversation_history", []):
                if m.get("role") != "assistant":
                    continue
                c = str(m.get("content", ""))
                # 匹配 ```<lang>? \n # / // path \n content \n ```
                # lang 可选: cpp / c++ / c / cmake / bash / sh / text / ini
                # 路径注释前缀: `//` (cpp/c) 或 `#` (cmake/bash/ini)
                import re

                for m_re in re.finditer(
                    r"```(?:cpp|c\+\+|c|cmake|bash|sh|text|ini)?\s*\n(?P<body>.*?)\n```",
                    c,
                    re.DOTALL,
                ):
                    body = m_re.group("body")
                    # 路径注释: // /path 或 # /path(支持跨行,避免 shebang 占用第一行)
                    # 路径必须以 / 开头(absolute),避免误匹配 `set -e`/`# comment` 等普通注释
                    # 搜前 500 字符(覆盖 bash 的 shebang 偏移)
                    pm = re.search(r"(?://|#)\s*(/[/\w.\-]+\.\S+)", body[:500])
                    if not pm:
                        continue
                    path = pm.group(1)
                    if path in existing_paths:
                        continue
                    # 剥第一行(路径注释)
                    content = "\n".join(body.split("\n")[1:]).strip()
                    if not content:
                        continue
                    existing_paths.add(path)
                    new_files.append({"path": path, "content": content, "tool": "markdown_block"})
                    # Markdown 落盘(file_write 落盘的对称行为,否则 operator_path_resolver
                    # rsync 一个空目录,LLM 输出不到 910B)。try/except 保护:测试无 I/O 不阻塞。
                    try:
                        from pathlib import Path as _P

                        _p = _P(path)
                        # U1:去掉 not exists,允许覆盖第 2 轮同 path 修复(原条件让旧文件残留)。
                        if _p.is_absolute():
                            _p.parent.mkdir(parents=True, exist_ok=True)
                            _p.write_text(content, encoding="utf-8")
                    except OSError:
                        pass

        if new_files:
            # U1:existing 中与新 file 同 path 的旧条目被覆盖(剔除),让 fix_loop 第 2 轮
            # 修复替换 code_result.files 里的旧 content(原 existing + new 会留两条同 path)。
            new_paths = {f["path"] for f in new_files}
            surviving_existing = [
                f
                for f in existing_files
                if not (isinstance(f, dict) and f.get("path") in new_paths)
            ]
            merged_files = surviving_existing + new_files

            # U5:LLM 命名飘移防御 —— rename 语义目录里用了错前缀的文件
            # (如 add_custom_def.cpp -> op_add_def.cpp)。
            op_info_for_rename = state.get("op_info") or {}
            op_snake = op_info_for_rename.get("name") or ""
            naming_renamed = 0
            if op_snake:
                merged_files, naming_renamed = enforce_op_naming(merged_files, op_snake)
            code_result["files"] = merged_files
            code_result["naming_renamed"] = naming_renamed

        update = {
            "messages": [{"role": "assistant", "content": response}],
            "memory_pools": new_memory_pools,
            "last_phase_result": {
                "phase": phase,
                "response": response,
                "naming_renamed": code_result.get("naming_renamed", 0) if new_files else 0,
            },
        }
        if new_files:
            update["code_result"] = code_result

        # U5:LLM response 里的 <<OP_INFO>>{json}<<END>> 块 → 写 state.op_info。
        # analyze 节点产,下游 codegen 节点读 state.op_info.name/class_name 命名文件。
        op_info = parse_op_info_block(response)
        if op_info is not None:
            update["op_info"] = op_info

        # 9. U2: SkillUsageRegistry 跟踪(signal-1)。
        # 加载侧:skill_names 显式记录(SKILL_BUNDLES 决定的)。
        # 使用侧:扫 _tool_calls_log 的 file_read 路径匹配 cannbot root。
        # 写入 registry(ephemeral,phase_callback 实时通知前端在 U6 接)。
        if skill_names is not None:
            try:
                from ascend_op_agent.orchestrator.cannbot_loader import (
                    SkillUsageRegistry,
                    extract_used_skills,
                )

                thread_id = state.get("thread_id", "")
                used = extract_used_skills(list(getattr(agent, "_tool_calls_log", [])))
                reg = SkillUsageRegistry.instance()
                reg.record_load(thread_id, phase, skill_names)
                reg.record_use(thread_id, phase, used)
                # 写入 update 供 U6 phase_callback 透传(同 thread 跨节点累积)
                update["skill_loads"] = {
                    "phase": phase,
                    "skill_names": list(skill_names),
                    "used_skills": used,
                }
            except Exception as e:  # 跟踪不应阻塞主流程
                import logging as _logging

                _logging.getLogger(__name__).warning(f"skill tracking failed (phase={phase}): {e}")

        return update

    return Node(name=phase, func=_node)
