"""Unified session schemas - 合并 sessions(聊天)+ checkpoints(工作流)成统一会话列表。

viewer 聚合层(方向 A):不改变两个存储(SessionRecordManager JSONL + CheckpointStore
SQLite),只在读取层合并成统一列表,供前端单 tab 展示。每条带 source 标识,详情按
source 走不同渲染(聊天->entry tree,工作流->state 面板)。
"""

from typing import Optional

from pydantic import BaseModel


class UnifiedSessionInfo(BaseModel):
    """统一会话列表项(聊天 + 工作流任务)。"""

    id: str  # session_id(聊天)或 thread_id(工作流)
    source: str  # chat / production / spike / e2e / migration / other
    label: str  # 显示用短 id
    updated_at: float  # 统一 timestamp(秒)便于排序
    updated_at_display: str  # ISO str 给前端显示
    phase: Optional[str] = None  # 工作流的 current_phase(聊天无)
    status: Optional[str] = None  # 工作流的 status(聊天无)
    db_file: Optional[str] = None  # 工作流的 db_file(详情查 checkpoint 用,聊天无)
    entry_count: int = 0  # 聊天的 entry 数(工作流无)


class UnifiedSessionListResponse(BaseModel):
    """Response for unified sessions list API."""

    sessions: list[UnifiedSessionInfo]
    total: int
