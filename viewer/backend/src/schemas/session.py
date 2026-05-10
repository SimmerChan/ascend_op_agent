"""Session schemas for API"""

from typing import Any, Optional
from pydantic import BaseModel, Field


class EntryBase(BaseModel):
    """Entry base schema"""
    id: str
    type: str
    timestamp: float
    session_id: str
    model: Optional[str] = None
    provider: Optional[str] = None
    turn_id: int = 0
    parent_id: Optional[str] = None


class UserEntrySchema(EntryBase):
    """User entry schema"""
    type: str = "user"
    content: str = ""
    token_count: int = 0


class SystemEntrySchema(EntryBase):
    """System entry schema"""
    type: str = "system"
    content: str = ""
    token_count: int = 0


class LLMEntrySchema(EntryBase):
    """LLM entry schema"""
    type: str = "assistant"
    input_messages: list[dict[str, str]] = Field(default_factory=list)
    output_content: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    token_count: int = 0


class ToolEntrySchema(EntryBase):
    """Tool entry schema"""
    type: str = "tool"
    tool_name: str = ""
    tool_call_id: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: Optional[str] = None
    token_count: int = 0


class SessionInfo(BaseModel):
    """Session info for list"""
    session_id: str
    source: str  # "acp" or "cli"
    created_at: float
    entry_count: int


class SessionListResponse(BaseModel):
    """Response for sessions list API"""
    sessions: list[SessionInfo]
    total: int


class TreeNode(BaseModel):
    """Tree node for nested structure"""
    id: str
    entry: dict[str, Any]
    children: list["TreeNode"] = Field(default_factory=list)


class SessionTreeResponse(BaseModel):
    """Response for session tree API"""
    tree: Optional[TreeNode] = None
    entries: list[dict[str, Any]] = Field(default_factory=list)


class SessionDetailResponse(BaseModel):
    """Response for session detail API"""
    session_id: str
    entries: list[dict[str, Any]]
    tree: Optional[TreeNode] = None