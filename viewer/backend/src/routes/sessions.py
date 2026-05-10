"""Session routes for API"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ..schemas.session import (
    SessionListResponse,
    SessionTreeResponse,
    SessionDetailResponse,
)
from ..services.tree_builder import TreeBuilder

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Singleton tree builder
_tree_builder: Optional[TreeBuilder] = None


def get_tree_builder() -> TreeBuilder:
    """Get or create tree builder singleton"""
    global _tree_builder
    if _tree_builder is None:
        _tree_builder = TreeBuilder()
    return _tree_builder


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    source: Optional[str] = Query(None, description="Filter by source: acp, cli, or all"),
    limit: int = Query(100, description="Maximum sessions to return"),
):
    """List all sessions with optional source filter"""
    builder = get_tree_builder()
    sessions = builder.list_sessions(limit)

    # Filter by source if specified
    if source and source != "all":
        sessions = [s for s in sessions if s.get("source") == source]

    return SessionListResponse(
        sessions=sessions,
        total=len(sessions),
    )


@router.get("/{session_id}/tree", response_model=SessionTreeResponse)
async def get_session_tree(session_id: str):
    """Get nested tree structure for a session"""
    builder = get_tree_builder()
    root_node, entries = builder.build_tree(session_id)

    if not entries:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionTreeResponse(
        tree=root_node.to_dict() if root_node else None,
        entries=[e.to_dict() for e in entries],
    )


@router.get("/{session_id}/entries", response_model=SessionDetailResponse)
async def get_session_entries(session_id: str):
    """Get flat list of entries for a session"""
    builder = get_tree_builder()
    entries = builder.get_entries(session_id)

    if not entries:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionDetailResponse(
        session_id=session_id,
        entries=[e.to_dict() for e in entries],
        tree=None,
    )


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(session_id: str):
    """Get session detail with tree structure"""
    builder = get_tree_builder()
    root_node, entries = builder.build_tree(session_id)

    if not entries:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionDetailResponse(
        session_id=session_id,
        entries=[e.to_dict() for e in entries],
        tree=root_node.to_dict() if root_node else None,
    )