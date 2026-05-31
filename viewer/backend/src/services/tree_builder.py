"""Tree builder service for constructing nested session tree"""

import sys
from pathlib import Path
from typing import Optional

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from ascend_op_agent.agent.session_record import (
    Entry,
    UserEntry,
    SystemEntry,
    LLMEntry,
    ToolEntry,
    entry_from_dict,
)
from ascend_op_agent.agent.session_manager import read_session_history

from ascend_op_agent.config import load_config


class TreeNode:
    """Tree node for nested structure"""

    def __init__(self, entry: Entry):
        self.entry = entry
        self.children: list[TreeNode] = []
        self.id = entry.id

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization"""
        return {
            "id": self.entry.id,
            "entry": self.entry.to_dict(),
            "children": [child.to_dict() for child in self.children],
        }


class TreeBuilder:
    """Build nested tree structure from flat entries"""

    def __init__(self, persist_dir: Optional[str] = None):
        if persist_dir is None:
            config = load_config()
            persist_dir = config.session.persist_dir
        self.persist_dir = persist_dir

    def build_tree(self, session_id: str) -> tuple[Optional[TreeNode], list[Entry]]:
        """Build tree from session entries

        Args:
            session_id: Session ID to load

        Returns:
            Tuple of (root_node, entries) where root_node is None if no entries.
            If multiple conversation turns exist, creates a virtual root node with
            each turn's user entry as a child.
        """
        entries = read_session_history(session_id, self.persist_dir)
        if not entries:
            return None, []

        # Build parent_id -> children mapping
        children_map: dict[str, list[Entry]] = {}
        root_entries: list[Entry] = []

        for entry in entries:
            parent_id = entry.parent_id
            if parent_id is None:
                root_entries.append(entry)
            else:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(entry)

        # Find all user entries (one per conversation turn)
        user_roots: list[Entry] = []
        for entry in entries:
            if entry.type == "user":
                user_roots.append(entry)

        if not user_roots:
            # No user entries, use first entry as root
            user_roots = [entries[0]] if entries else []

        # Build tree recursively
        def build_node(entry: Entry) -> TreeNode:
            node = TreeNode(entry)
            children = children_map.get(entry.id, [])
            for child in children:
                node.children.append(build_node(child))
            return node

        if len(user_roots) == 1:
            # Single turn: build normal tree
            root_node = build_node(user_roots[0])
        else:
            # Multiple turns: create virtual root node
            # Use first entry as base for the virtual root
            virtual_root = Entry(
                type="turn",
                id="conversation-root",
                session_id=session_id,
                parent_id=None,
            )
            root_node = TreeNode(virtual_root)
            for user_root in user_roots:
                root_node.children.append(build_node(user_root))

        return root_node, entries

    def get_entries(self, session_id: str) -> list[Entry]:
        """Get flat list of entries for a session"""
        return read_session_history(session_id, self.persist_dir)

    def list_sessions(self, limit: int = 100) -> list[dict]:
        """List all sessions with metadata

        Returns:
            List of session info dicts with session_id, source, created_at, entry_count
        """
        from ascend_op_agent.agent.session_manager import list_sessions
        sessions = list_sessions(self.persist_dir, limit)

        result = []
        for sess in sessions:
            session_id = sess.get("session_id", "")
            entries = self.get_entries(session_id)
            entry_count = len(entries)

            # Determine source from session_id prefix or metadata
            source = "cli"  # default
            if session_id.startswith("acp_"):
                source = "acp"

            # Find earliest timestamp for created_at
            created_at = 0.0
            for entry in entries:
                if created_at == 0.0 or entry.timestamp < created_at:
                    created_at = entry.timestamp

            result.append({
                "session_id": session_id,
                "source": source,
                "created_at": created_at,
                "entry_count": entry_count,
            })

        return result