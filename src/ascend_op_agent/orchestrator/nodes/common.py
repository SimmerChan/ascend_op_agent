"""make_llm_node —— LLM 节点工厂(实现 P0-2 共存契约)。

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


def make_llm_node(
    phase: str,
    task_prompt_template: str,
    skill_bundle_text: Optional[str] = None,
    agent_factory: Optional[AgentFactory] = None,
    template_vars: Optional[dict] = None,
    skill_names: Optional[list[str]] = None,
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
        existing_paths = {f.get("path") for f in existing_files if isinstance(f, dict)}
        new_files = [
            {
                "path": entry["args"].get("path", ""),
                "content": entry["args"].get("content", ""),
                "tool": entry["name"],
            }
            for entry in getattr(agent, "_tool_calls_log", [])
            if entry.get("name") == "file_write"
            and entry.get("args", {}).get("path")
            and entry["args"]["path"] not in existing_paths
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
                        if _p.is_absolute() and not _p.exists():
                            _p.parent.mkdir(parents=True, exist_ok=True)
                            _p.write_text(content, encoding="utf-8")
                    except OSError:
                        pass

        if new_files:
            code_result["files"] = existing_files + new_files

        update = {
            "messages": [{"role": "assistant", "content": response}],
            "memory_pools": new_memory_pools,
            "last_phase_result": {"phase": phase, "response": response},
        }
        if new_files:
            update["code_result"] = code_result

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
