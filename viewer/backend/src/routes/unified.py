"""Unified session routes - 合并 sessions(聊天)+ checkpoints(工作流)成统一会话列表。

方向 A(viewer 聚合):前端单 tab 调本 endpoint 拿统一列表,详情按 source 路由
(聊天 -> /api/sessions/{id}/tree,工作流 -> /api/checkpoints/{db_file}/{thread_id})。
"""

from typing import Optional

from fastapi import APIRouter, Query

from schemas.unified import UnifiedSessionInfo, UnifiedSessionListResponse
from services.unified_reader import list_unified_sessions

router = APIRouter(prefix="/api/unified-sessions", tags=["unified-sessions"])


@router.get("", response_model=UnifiedSessionListResponse)
async def list_unified(
    source: Optional[str] = Query(
        None,
        description="Filter: chat/production/spike/e2e/migration/other/all",
    ),
    limit: int = Query(200, description="Maximum sessions to return"),
):
    """List all sessions (chat + workflow) merged across SessionRecordManager + CheckpointStore."""
    items = list_unified_sessions(limit=limit)
    if source and source != "all":
        items = [i for i in items if i["source"] == source]
    return UnifiedSessionListResponse(
        sessions=[UnifiedSessionInfo(**i) for i in items],
        total=len(items),
    )
