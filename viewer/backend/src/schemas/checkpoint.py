"""Checkpoint schemas for API — viewer 读 CheckpointStore(state 快照模型)。

区别于 session.py(Entry 事件流模型,SessionRecordManager JSONL),本 schema
对应 CheckpointStore SQLite 的 state 快照,含完整 code_result / compile_result /
precision_report 等算子开发结构化字段。覆盖生产 op: / spike / e2e 三条路径。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class CheckpointInfo(BaseModel):
    """Checkpoint 列表项(跨多 db 聚合,每条带 db_file + source)。"""

    db_file: str
    thread_id: str
    current_phase: Optional[str] = None
    status: str
    updated_at: str
    source: str  # production / spike / e2e / other


class CheckpointListResponse(BaseModel):
    """Response for checkpoints list API."""

    checkpoints: list[CheckpointInfo]
    total: int


class CheckpointStateResponse(BaseModel):
    """单个 checkpoint 完整 state —— viewer 详情页渲染用。

    state_json 顶层 15 字段里,viewer 主要渲染这几个结构化大字段:
    messages(对话历史)/ code_result(LLM 产出代码)/ compile_result(编译日志)/
    precision_report(ST 精度)/ phase_history(阶段轨迹)。
    """

    db_file: str
    thread_id: str
    current_phase: Optional[str] = None
    status: Optional[str] = None
    updated_at: Optional[str] = None
    source: str
    op_info: Optional[dict[str, Any]] = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    code_result: Optional[dict[str, Any]] = None
    compile_result: Optional[dict[str, Any]] = None
    precision_report: Optional[dict[str, Any]] = None
    phase_history: list[Any] = Field(default_factory=list)
    skill_loads: Optional[dict[str, Any]] = None
    pending_confirmation: Optional[dict[str, Any]] = None
