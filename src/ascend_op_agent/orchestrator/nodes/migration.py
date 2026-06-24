"""迁移图节点(B1 CUDA / B2 Triton 共用)。

前端解析节点(frontend_parse)职责:

1. scoped cannbot skill(CUDA: cuda2ascend-simt;Triton: 5-skill 链入口)
2. prompt 指示 LLM 把源代码解析成 ``OpInfo`` + ``ArchitectureMapping``
3. 节点后置从 LLM 响应中抽取严格 JSON,写入 ``state["op_info"]`` 和
   ``state["design_doc"]["arch_mapping"]``

输出契约:

- ``state["op_info"]``: dict,含 ``migration_strategy`` = "cuda_to_ascendc"
  或 "triton_to_ascendc"
- ``state["design_doc"]["arch_mapping"]``: dict,含 ``source_type`` 和 ``mappings``

LLM 不输出有效 JSON 时降级 —— 记录 ``state["last_phase_result"]["parse_error"]``,
不阻塞图执行(后续 design 节点会基于不完整信息补全)。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ascend_op_agent.orchestrator.nodes.common import AgentFactory, make_llm_node
from ascend_op_agent.orchestrator.state_machine import Node


_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


def extract_structured_output(response: str) -> dict:
    """从 LLM 响应中提取结构化 JSON。

    支持两种格式:

    - 优先:`````json ... `````(代码块包裹)
    - 兜底:裸 JSON(整个响应是 ``{...}``)

    Returns:
        ``{"op_info": dict|None, "arch_mapping": dict|None, "raw": str,
           "parse_error": str|None}`` —— parse_error 为 None 表示解析成功
    """
    result: dict[str, Any] = {
        "op_info": None,
        "arch_mapping": None,
        "raw": response,
        "parse_error": None,
    }

    match = _JSON_BLOCK_RE.search(response)
    json_str: Optional[str] = match.group(1) if match else None

    if json_str is None:
        # 兜底:尝试裸 JSON
        bare = _BARE_JSON_RE.search(response)
        if bare is not None:
            json_str = bare.group(1)

    if json_str is None:
        result["parse_error"] = "no JSON block found in LLM response"
        return result

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        result["parse_error"] = f"JSON decode error: {e}"
        return result

    if not isinstance(data, dict):
        result["parse_error"] = "parsed JSON is not a dict"
        return result

    op_info = data.get("op_info")
    arch_mapping = data.get("arch_mapping")
    if isinstance(op_info, dict):
        result["op_info"] = op_info
    if isinstance(arch_mapping, dict):
        result["arch_mapping"] = arch_mapping
    return result


_CUDA_FRONTEND_PROMPT = """你是 CUDA → Ascend C SIMT 迁移专家。
参考 cannbot cuda2ascend-simt skill 的 API 映射表和约束规则,分析以下 CUDA 算子源码/描述,
产出迁移决策。

输入(CUDA 代码或算子描述):
{user_input}

要求:

1. 识别算子名、输入/输出 shape 与 dtype
2. ``migration_strategy`` 必须为 ``"cuda_to_ascendc"``
3. ``ref_code_type`` = ``"cuda"``
4. ``arch_mapping.mappings`` 至少包含一个 CUDA → Ascend C API 对应
   (从 cuda2ascend-simt reference/api-mapping 查得)

请严格用以下 JSON 格式输出(用 ```json 包裹):

```json
{{
  "op_info": {{
    "name": "<算子名>",
    "description": "<一句话描述>",
    "op_type": "<elementwise/reduction/matmul/...",
    "input_shapes": [[...], ...],
    "input_dtypes": ["float16", ...],
    "output_shapes": [[...], ...],
    "output_dtypes": ["float16", ...],
    "migration_strategy": "cuda_to_ascendc",
    "ref_code_type": "cuda"
  }},
  "arch_mapping": {{
    "source_type": "cuda",
    "mappings": {{
      "<cuda_api>": "<ascend_equivalent>",
      ...
    }}
  }}
}}
```"""


def make_cuda_frontend_node(
    agent_factory: AgentFactory,
    skill_bundle_text: Optional[str] = None,
    phase_name: str = "cuda_frontend",
) -> Node:
    """构造 CUDA 前端解析节点。

    Args:
        agent_factory: 返回 fresh AIAgent 的工厂
        skill_bundle_text: cuda2ascend-simt skill 文本(Layer 6 注入);
            None 时用默认 Layer 6
        phase_name: 节点名(默认 ``cuda_frontend``)

    Returns:
        Node —— 跑完 LLM 后,从响应抽 JSON 写入 state
    """
    base_node = make_llm_node(
        phase=phase_name,
        task_prompt_template=_CUDA_FRONTEND_PROMPT,
        skill_bundle_text=skill_bundle_text,
        agent_factory=agent_factory,
    )

    def _frontend(state: dict) -> dict:
        update = base_node.func(state)

        # pending resume 路径:base_node 已跳过 LLM,无需 parse
        if state.get("pending_confirmation") is not None:
            return update

        response = (update.get("last_phase_result") or {}).get("response", "")
        parsed = extract_structured_output(response)

        if parsed["op_info"] is not None:
            update["op_info"] = parsed["op_info"]

        if parsed["arch_mapping"] is not None:
            existing_design = state.get("design_doc") or {}
            # 浅拷贝避免 mutation,merge arch_mapping
            merged_design = dict(existing_design)
            merged_design["arch_mapping"] = parsed["arch_mapping"]
            update["design_doc"] = merged_design

        # 把 parse 错误透传到 last_phase_result(便于调试,不阻塞)
        if parsed["parse_error"] is not None:
            lpr = dict(update.get("last_phase_result") or {})
            lpr["parse_error"] = parsed["parse_error"]
            update["last_phase_result"] = lpr

        return update

    return Node(name=phase_name, func=_frontend)
