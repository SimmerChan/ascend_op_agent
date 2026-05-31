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
            Uses chronological ordering since all entries have parent_id=None.
        """
        entries = read_session_history(session_id, self.persist_dir)
        if not entries:
            return None, []

        # Find all user entries as conversation roots
        user_entries: list[Entry] = [e for e in entries if e.type == "user"]
        if not user_entries:
            user_entries = [entries[0]] if entries else []

        # Build tree using chronological order
        # All entries between two user entries belong to the first user entry
        def build_node(entry: Entry) -> TreeNode:
            node = TreeNode(entry)
            return node

        if len(user_entries) == 1:
            # Single turn - all entries after user belong to it
            root_node = build_node(user_entries[0])
            user_idx = entries.index(user_entries[0])
            for i in range(user_idx + 1, len(entries)):
                child_node = build_node(entries[i])
                root_node.children.append(child_node)
        else:
            # Multiple turns - create virtual root
            virtual_root = Entry(
                type="turn",
                id="conversation-root",
                session_id=session_id,
                parent_id=None,
            )
            root_node = TreeNode(virtual_root)

            for turn_idx, user_entry in enumerate(user_entries):
                turn_start = entries.index(user_entry)
                turn_end = entries.index(user_entries[turn_idx + 1]) if turn_idx + 1 < len(user_entries) else len(entries)

                turn_node = build_node(user_entry)
                for i in range(turn_start + 1, turn_end):
                    child_node = build_node(entries[i])
                    turn_node.children.append(child_node)

                root_node.children.append(turn_node)

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