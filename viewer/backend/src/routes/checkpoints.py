"""Checkpoint routes —— viewer 读 CheckpointStore(state 快照)。

区别于 sessions router(读 SessionRecordManager JSONL 事件流,只覆盖聊天路径),
本 router 读 CheckpointStore SQLite state 快照,覆盖**所有算子开发路径**:
生产 op: / spike / e2e。两条 router 共存,前端按来源分组。

Routes:
- ``GET /api/checkpoints``                — 跨多 db 聚合 thread 列表(?source=过滤)
- ``GET /api/checkpoints/{db_file}/{thread_id}`` — 单 thread 完整 state
  (messages + code_result + compile_result + precision_report + phase_history)
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from schemas.checkpoint import (
    CheckpointInfo,
    CheckpointListResponse,
    CheckpointStateResponse,
)
from services.ckpt_reader import get_checkpoint_state, list_all_checkpoints

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])


@router.get("", response_model=CheckpointListResponse)
async def list_checkpoints(
    source: Optional[str] = Query(
        None, description="Filter: production, spike, e2e, other, or all"
    ),
):
    """List all checkpoints across all checkpoint dbs (production + spike + e2e)."""
    items = list_all_checkpoints()
    if source and source != "all":
        items = [c for c in items if c["source"] == source]
    return CheckpointListResponse(
        checkpoints=[CheckpointInfo(**c) for c in items],
        total=len(items),
    )


@router.get("/{db_file}/{thread_id}", response_model=CheckpointStateResponse)
async def get_checkpoint_detail(db_file: str, thread_id: str):
    """Get full state for a checkpoint (messages + code + compile + precision)."""
    result = get_checkpoint_state(db_file, thread_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint not found: {db_file}/{thread_id}",
        )
    state = result["state"]
    return CheckpointStateResponse(
        db_file=db_file,
        thread_id=thread_id,
        current_phase=result.get("current_phase"),
        status=result.get("status"),
        updated_at=result.get("updated_at"),
        source=result.get("source", "other"),
        op_info=state.get("op_info"),
        messages=state.get("messages") or [],
        code_result=state.get("code_result"),
        compile_result=state.get("compile_result"),
        precision_report=state.get("precision_report"),
        phase_history=state.get("phase_history") or [],
        skill_loads=state.get("skill_loads"),
        pending_confirmation=state.get("pending_confirmation"),
    )
