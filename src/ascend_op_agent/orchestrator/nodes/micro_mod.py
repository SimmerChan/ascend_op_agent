"""U5: LLM micro-modification 节点(基于 scaffold 加载,多文件版本)。

场景:codegen 节点从 scaffold 加载了算子工程(如 add_example),LLM 后续
可在此基础上做小改(加 broadcasting / 改 dtype 支持 / 改性能策略)。
多文件 vs 单文件:某些修改(如 broadcasting)需要改 kernel + op_host +
op_kernel.ini,单文件不够。prompt 明确告诉 LLM 所有 target 文件,LLM 调多次
file_write 改之。

风险:55% LLM 失败率(单文件时代码经验)。所以默认 opt-in (False),
P0 验收不依赖 micro_mod 成功。U7 e2e 显式 --with-micro-mod 启用。

设计原则:
- 自包含 node(不依赖 make_llm_node),因为 micro_mod 有不同的 prompt 构造
  逻辑(嵌入多文件内容 + 限制改 target_files 范围)
- file_write 捕获逻辑同 make_llm_node:扫 agent._tool_calls_log
- 区分 target_files 命中(替换) vs 其它路径(追加)
- 检测失败:无 file_write 调用 → micro_mod_result.success=False
"""

from __future__ import annotations

import logging
from typing import Optional

from ascend_op_agent.orchestrator.state_machine import Node

logger = logging.getLogger(__name__)


def make_micro_mod_node(
    phase: str,
    target_files: list[str],
    instruction: str,
    agent_factory,
    operator_dir: str = "/tmp/e2e_ops_local/op_add",
    skill_bundle_text: Optional[str] = None,
    skill_names: Optional[list[str]] = None,
) -> Node:
    """构造 micro-modification 节点(多文件版本)。

    节点流程:
    1. 读 state["code_result"]["files"] 找 target_files 命中项
    2. 嵌入文件 content + 改造指令到 prompt
    3. 调 LLM(单次 ReAct 循环,LLM 调多次 file_write)
    4. 抓 _tool_calls_log 的 file_write
    5. 更新 state["code_result"]["files"]:target 命中 → 替换,其它 → 追加
    6. 写 state["micro_mod_result"]:success/files_changed

    Args:
        phase: 阶段名(用作 current_phase / phase_callback)
        target_files: 允许 LLM 改的文件路径(绝对路径或 operator_dir 相对路径)
        instruction: 改造指令(如"加 broadcasting 支持")
        agent_factory: 返回 fresh AIAgent 的工厂
        operator_dir: 算子工程根目录(用于解析相对 target_files)
        skill_bundle_text: cannbot skill 文本(注入 Layer 6)
        skill_names: cannbot skill 名(供 U2 SkillUsageRegistry 跟踪)

    Returns:
        Node —— 跑完写 state["code_result"] + state["micro_mod_result"]
    """
    # 把 target_files 转成绝对路径(LLM 用绝对路径调 file_write)
    abs_targets = []
    for f in target_files:
        if f.startswith("/"):
            abs_targets.append(f)
        else:
            abs_targets.append(f"{operator_dir}/{f}")
    abs_set = set(abs_targets)

    def _build_prompt(state: dict) -> str:
        code_result = state.get("code_result") or {}
        files = code_result.get("files") or []

        # 找 target_files 对应的文件内容
        target_contents = []
        for target in abs_targets:
            for f in files:
                if isinstance(f, dict) and f.get("path") == target:
                    content = f.get("content", "")
                    target_contents.append(f"### {target}\n```cpp\n{content}\n```\n")
                    break

        targets_block = "\n".join(target_contents) if target_contents else "(未找到 target_files 内容)"
        targets_list = "\n".join(f"- {t}" for t in abs_targets)
        return (
            f"你是 Ascend C 算子 developer。基于已加载的 scaffold 做精准小改:\n\n"
            f"【改造指令】\n{instruction}\n\n"
            f"【目标文件(必须用 file_write 工具改这些)】\n{targets_list}\n\n"
            f"【目标文件内容】\n{targets_block}\n\n"
            f"【必须】用 file_write 工具(参数:path=绝对路径,content=完整修改后内容)逐个改目标文件。\n"
            f"【禁止】把代码贴在 assistant 文本。\n"
            f"【禁止】改 target_files 列表以外的文件。\n"
            f"【完成后】回 1 字符 'k' 表示 keep going。"
        )

    def _node(state: dict) -> dict:
        agent = agent_factory()
        # rehydrate 对话历史(同 make_llm_node 模式,保 P0-2 共存契约)
        agent._conversation_history = list(state.get("messages", []))
        existing_memory = state.get("memory_pools", {})
        for pool, items in existing_memory.items():
            if isinstance(items, list):
                for item in items:
                    agent.memory.add(pool, item)

        # 构造 prompt 并调 LLM
        task_prompt = _build_prompt(state)
        response = agent.run_conversation(task_prompt, skills_layer_override=skill_bundle_text)

        # 抓 memory 快照
        new_memory_pools: dict[str, list[str]] = dict(existing_memory)
        for pool in ("memory", "user"):
            items = agent.memory.get(pool)
            if items:
                new_memory_pools[pool] = list(items)

        # 抓 file_write 调用
        tool_calls = list(getattr(agent, "_tool_calls_log", []))
        new_writes = [
            {
                "path": entry["args"].get("path", ""),
                "content": entry["args"].get("content", ""),
                "tool": entry["name"],
            }
            for entry in tool_calls
            if entry.get("name") == "file_write"
            and entry.get("args", {}).get("path")
        ]

        # 区分 target_files 命中 vs 其它路径
        code_result = dict(state.get("code_result") or {})
        existing_files = list(code_result.get("files") or [])
        existing_paths = {f.get("path") for f in existing_files if isinstance(f, dict)}

        new_target_entries = []  # 替换 target_files 中已有项
        new_other_entries = []   # 追加到 files(target 之外)
        for nw in new_writes:
            p = nw["path"]
            if p in abs_set:
                # target 命中 → 找原 entry 替换(保留任何非 path/content 字段)
                replaced = False
                for i, ef in enumerate(existing_files):
                    if isinstance(ef, dict) and ef.get("path") == p:
                        existing_files[i] = {**ef, **nw, "tool": "micro_mod"}
                        replaced = True
                        break
                if not replaced:
                    # target 在 prompt 里但 state 里没有(异常),追加
                    existing_files.append({**nw, "tool": "micro_mod"})
                    new_target_entries.append(p)
                else:
                    new_target_entries.append(p)
            else:
                # 非 target 路径 → 追加
                if p not in existing_paths:
                    existing_files.append({**nw, "tool": "micro_mod"})
                    new_other_entries.append(p)

        # 写 micro_mod_result
        micro_mod_result = {
            "success": len(new_target_entries) > 0,
            "files_changed": new_target_entries,
            "files_added_unexpected": new_other_entries,
            "target_files": list(abs_targets),
            "llm_response": response[:500] if response else "",
        }
        if not micro_mod_result["success"]:
            logger.warning(
                f"micro_mod[{phase}] 失败:LLM 没改 target_files({abs_targets}),"
                f"file_write 调用了 {len(new_writes)} 个文件"
            )

        update = {
            "messages": [{"role": "assistant", "content": response}],
            "memory_pools": new_memory_pools,
            "last_phase_result": {
                "phase": phase,
                "response": response,
                "kind": "micro_mod",
            },
            "code_result": {**code_result, "files": existing_files},
            "micro_mod_result": micro_mod_result,
        }

        # U2: SkillUsageRegistry 跟踪
        if skill_names is not None:
            try:
                from ascend_op_agent.orchestrator.cannbot_loader import (
                    SkillUsageRegistry,
                    extract_used_skills,
                )

                thread_id = state.get("thread_id", "")
                used = extract_used_skills(tool_calls)
                reg = SkillUsageRegistry.instance()
                reg.record_load(thread_id, phase, skill_names)
                reg.record_use(thread_id, phase, used)
                update["skill_loads"] = {
                    "phase": phase,
                    "skill_names": list(skill_names),
                    "used_skills": used,
                }
            except Exception as e:
                logger.warning(f"micro_mod skill tracking failed: {e}")

        return update

    return Node(name=phase, func=_node)


def make_micro_mod_node_opt_in(
    phase: str,
    target_files: list[str],
    instruction: str,
    agent_factory,
    enabled: bool = False,
    **kwargs,
) -> Optional[Node]:
    """opt-in 包装:enabled=False 返回 None(节点跳过)。"""
    if not enabled:
        return None
    return make_micro_mod_node(
        phase=phase,
        target_files=target_files,
        instruction=instruction,
        agent_factory=agent_factory,
        **kwargs,
    )
