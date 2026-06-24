"""交付模式选择节点 + 可选框架适配节点(U12)。

设计要点:

1. **delivery_mode_node**:非 LLM 节点(纯 Python 启发式 + HITL interrupt)
   - 启发式扫 code_result.files 找 torch_npu / torch import / PYBIND11_MODULE
     关键字,推荐模式
   - emit ``__interrupt__`` 让用户在 sample / torch_npu / pybind 三选一
   - resume 后写 ``state["delivery_mode"]`` 推进
2. **framework_adapt_node**:LLM 节点(scoped ascendc-direct-invoke-template
   skill),根据 ``delivery_mode`` 决定实际跑或跳过
   - ``torch_npu``:真实跑 LLM 生成 torch_npu 集成代码
   - ``sample`` / ``pybind``:返回 ``{"skipped": True, ...}``,PhaseRunner 继续

PhaseRunner 是顺序 DAG 不支持条件路由,跳过逻辑在节点函数体内完成。
"""

from __future__ import annotations

from typing import Any, Optional

from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.state_machine import Node


# ---- 启发式 ----


_TORCH_NPU_HINTS = ("torch_npu", "import torch_npu")
_TORCH_EXT_HINTS = ("torch.h", "<torch/extension.h>", "PYBIND11_MODULE", "pybind11")
_VALID_MODES = ("sample", "torch_npu", "pybind")


def recommend_delivery_mode(state: dict) -> str:
    """扫 code_result.files 推荐 delivery_mode。

    - 含 ``torch_npu`` import → ``"torch_npu"``
    - 含 torch.h / pybind 关键字 → ``"pybind"``
    - 否则(纯 kernel) → ``"sample"``

    缺 code_result 时默认 ``"sample"``。
    """
    code_result = state.get("code_result") or {}
    files = code_result.get("files") or []
    blob_parts: list[str] = []
    for f in files:
        if isinstance(f, dict):
            blob_parts.append(str(f.get("content") or ""))
            blob_parts.append(str(f.get("path") or ""))
        elif isinstance(f, str):
            blob_parts.append(f)
    blob = "\n".join(blob_parts)

    if any(hint in blob for hint in _TORCH_NPU_HINTS):
        return "torch_npu"
    if any(hint in blob for hint in _TORCH_EXT_HINTS):
        return "pybind"
    return "sample"


# ---- delivery_mode 节点 ----


def make_delivery_mode_node(phase: str = "delivery_mode") -> Node:
    """构造交付模式选择节点(非 LLM,HITL interrupt)。

    Returns:
        Node —— 首次跑产 __interrupt__(含 3 选项 + 推荐项);
        resume 后写 state["delivery_mode"] 推进
    """

    def _delivery_mode(state: dict) -> dict:
        pending = state.get("pending_confirmation")
        if pending is not None:
            mode = (pending or {}).get("mode", "sample")
            if mode not in _VALID_MODES:
                # 兜底:非法 mode 回退 sample(用户输入异常时不阻塞)
                mode = "sample"
            return {
                "delivery_mode": mode,
                "pending_confirmation": None,
            }

        # 首次跑:启发式 + HITL interrupt
        recommended = recommend_delivery_mode(state)
        payload = {
            "phase": phase,
            "message": "请选择交付模式(sample 直接编译验证 / "
            "torch_npu 框架适配 / pybind Python 绑定)",
            "options": list(_VALID_MODES),
            "recommended": recommended,
            "op_info": state.get("op_info"),
            "code_result_summary": _summarize_code_result(state.get("code_result")),
        }
        return {"__interrupt__": payload}

    return Node(name=phase, func=_delivery_mode)


def _summarize_code_result(code_result: Optional[dict]) -> dict:
    """提取 code_result 摘要(避免 interrupt payload 过大)。"""
    if not code_result:
        return {}
    files = code_result.get("files") or []
    return {
        "file_count": len(files) if isinstance(files, list) else 0,
        "file_names": [
            (f.get("path") or f.get("name") or "<unnamed>")
            for f in files
            if isinstance(f, dict)
        ],
    }


# ---- framework_adapt 节点 ----


_FRAMEWORK_ADAPT_PROMPT = """你是 Ascend C → torch_npu 框架适配专家。
基于已编译通过的 AscendC kernel 产出 torch_npu 集成代码:

状态(OpInfo + code_result + 历史):
{state}

输出:
1. ``ascend_extension.cpp``:torch_npu Custom OpPath 注册代码
2. ``setup.py``:构建脚本
3. 集成调用示例(``import torch_npu; torch.npu.compile_ops(...)``)

参考 cannbot ascendc-direct-invoke-template skill 的 grammar 与约束。
"""


def make_framework_adapt_node(
    phase: str = "framework_adapt",
    agent_factory: Optional[AgentFactory] = None,
    skill_bundle_text: Optional[str] = None,
) -> Node:
    """构造框架适配节点(仅 torch_npu 模式实际执行)。

    Args:
        phase: 节点名(默认 ``framework_adapt``)
        agent_factory: 返回 fresh AIAgent 的工厂;None 时跑占位逻辑
        skill_bundle_text: cannbot skill 文本(传 make_llm_node)

    Returns:
        Node —— torch_npu 模式跑 LLM;sample/pybind 模式跳过
    """
    base_node: Optional[Node] = None
    if agent_factory is not None:
        base_node = make_llm_node(
            phase=phase,
            task_prompt_template=_FRAMEWORK_ADAPT_PROMPT,
            skill_bundle_text=skill_bundle_text,
            agent_factory=agent_factory,
        )

    def _framework_adapt(state: dict) -> dict:
        mode = state.get("delivery_mode", "sample")
        if mode != "torch_npu":
            return {
                "framework_adapt_result": {
                    "skipped": True,
                    "reason": f"delivery_mode={mode}, 仅 torch_npu 需要框架适配",
                }
            }

        if base_node is None:
            return {
                "framework_adapt_result": {
                    "skipped": False,
                    "mode": "torch_npu",
                    "note": "agent_factory not provided (placeholder)",
                }
            }

        update = base_node.func(state)
        fr = dict(update.get("framework_adapt_result") or {})
        fr["skipped"] = False
        fr["mode"] = "torch_npu"
        update["framework_adapt_result"] = fr
        return update

    return Node(name=phase, func=_framework_adapt)
