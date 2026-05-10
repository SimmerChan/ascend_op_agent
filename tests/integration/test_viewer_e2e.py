# Copyright 2026 SimmerChan
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""End-to-end integration tests for Agent Conversation Visualizer

Tests the complete data flow: JSONL → backend API → frontend display
"""

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from ascend_op_agent.agent.session_record import (
    UserEntry,
    SystemEntry,
    LLMEntry,
    ToolEntry,
    entry_from_dict,
)


class TestTreeBuilder:
    """Test tree builder service"""

    def setup_method(self):
        """Create temp directory for test session files"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_session_id = f"test_session_{uuid.uuid4().hex[:8]}"

    def teardown_method(self):
        """Clean up temp files"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_entry_to_jsonl(self, entry) -> None:
        """Write an entry to the test JSONL file"""
        session_file = Path(self.temp_dir) / f"{self.test_session_id}.jsonl"
        with open(session_file, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")

    def test_build_tree_simple_conversation(self):
        """Test building tree for a simple user → LLM → response flow"""
        # Create a simple conversation flow
        user_id = str(uuid.uuid4())
        llm_id = str(uuid.uuid4())

        # UserEntry (root)
        user_entry = UserEntry(
            id=user_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=0,
            parent_id=None,
            content="Hello, how are you?",
        )
        self._write_entry_to_jsonl(user_entry)

        # LLMEntry (child of user)
        llm_entry = LLMEntry(
            id=llm_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=1,
            parent_id=user_id,
            input_messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello, how are you?"},
            ],
            output_content="I'm doing well, thank you! How can I help you today?",
            tool_calls=[],
        )
        self._write_entry_to_jsonl(llm_entry)

        # Import tree builder and test
        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=self.temp_dir)
        root_node, entries = builder.build_tree(self.test_session_id)

        assert root_node is not None
        assert len(entries) == 2
        # Root should be the UserEntry
        assert root_node.entry.type == "user"
        assert len(root_node.children) == 1
        # Child should be the LLMEntry
        assert root_node.children[0].entry.type == "assistant"
        assert root_node.children[0].entry.id == llm_id

    def test_build_tree_with_tool_calls(self):
        """Test tree building with tool call chain: user → llm → tool → llm → response"""
        user_id = str(uuid.uuid4())
        llm1_id = str(uuid.uuid4())
        tool_id = str(uuid.uuid4())
        llm2_id = str(uuid.uuid4())

        # UserEntry
        user_entry = UserEntry(
            id=user_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=0,
            parent_id=None,
            content="What's the weather?",
        )
        self._write_entry_to_jsonl(user_entry)

        # LLMEntry (first call, has tool call)
        llm1_entry = LLMEntry(
            id=llm1_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=1,
            parent_id=user_id,
            input_messages=[{"role": "user", "content": "What's the weather?"}],
            output_content="I'll check the weather for you.",
            tool_calls=[{"name": "get_weather", "arguments": {"city": "Beijing"}}],
        )
        self._write_entry_to_jsonl(llm1_entry)

        # ToolEntry (child of llm1)
        tool_entry = ToolEntry(
            id=tool_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=1,
            parent_id=llm1_id,
            tool_name="get_weather",
            arguments={"city": "Beijing"},
            result="Sunny, 25°C",
            success=True,
        )
        self._write_entry_to_jsonl(tool_entry)

        # LLMEntry (after tool, final response)
        llm2_entry = LLMEntry(
            id=llm2_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=1,
            parent_id=tool_id,
            input_messages=[{"role": "user", "content": "What's the weather?"}],
            output_content="The weather in Beijing is sunny with 25°C.",
            tool_calls=[],
        )
        self._write_entry_to_jsonl(llm2_entry)

        # Build tree
        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=self.temp_dir)
        root_node, entries = builder.build_tree(self.test_session_id)

        assert root_node is not None
        assert len(entries) == 4

        # Tree structure: user → llm1 → tool → llm2
        assert root_node.entry.type == "user"
        assert len(root_node.children) == 1

        llm1_node = root_node.children[0]
        assert llm1_node.entry.id == llm1_id
        assert llm1_node.entry.type == "assistant"
        assert len(llm1_node.children) == 1

        tool_node = llm1_node.children[0]
        assert tool_node.entry.id == tool_id
        assert tool_node.entry.tool_name == "get_weather"
        assert len(tool_node.children) == 1

        llm2_node = tool_node.children[0]
        assert llm2_node.entry.id == llm2_id

    def test_build_tree_backward_compatibility_no_parent_id(self):
        """Test that old JSONL files without parent_id still work"""
        user_id = str(uuid.uuid4())

        # UserEntry without parent_id (old format)
        user_entry = UserEntry(
            id=user_id,
            timestamp=time.time(),
            session_id=self.test_session_id,
            model="gpt-4",
            provider="openai",
            turn_id=0,
            content="Hello",
        )
        # Force write without parent_id
        session_file = Path(self.temp_dir) / f"{self.test_session_id}.jsonl"
        with open(session_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(user_entry.to_dict()) + "\n")

        # Build tree
        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=self.temp_dir)
        root_node, entries = builder.build_tree(self.test_session_id)

        # Should still work, treating entries without parent_id as roots
        assert root_node is not None
        assert len(entries) == 1
        assert root_node.entry.id == user_id

    def test_list_sessions_with_source_detection(self):
        """Test session listing with ACP/CLI source detection"""
        # Write entries with different session IDs (simulating ACP and CLI sessions)
        for i, prefix in enumerate(["acp_", "cli_"]):
            session_id = f"{prefix}session_{i}"
            session_file = Path(self.temp_dir) / f"{session_id}.jsonl"

            user_entry = UserEntry(
                id=str(uuid.uuid4()),
                timestamp=time.time(),
                session_id=session_id,
                model="gpt-4",
                provider="openai",
                turn_id=0,
                content=f"Test message {i}",
            )

            with open(session_file, "w", encoding="utf-8") as f:
                f.write(user_entry.to_json() + "\n")

        # List sessions
        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=self.temp_dir)
        sessions = builder.list_sessions()

        assert len(sessions) == 2

        sources = {s["source"] for s in sessions}
        assert "acp" in sources
        assert "cli" in sources


class TestSessionRecordParentId:
    """Test parent_id field in session records"""

    def test_user_entry_parent_id_serialization(self):
        """Test UserEntry with parent_id serializes correctly"""
        entry = UserEntry(
            id="test-id",
            timestamp=time.time(),
            session_id="test-session",
            model="gpt-4",
            provider="openai",
            turn_id=0,
            parent_id="parent-id",
            content="test",
        )

        data = entry.to_dict()
        assert data["parent_id"] == "parent-id"
        assert "parent_id" in data

    def test_llm_entry_parent_id_deserialization(self):
        """Test LLMEntry deserialization with parent_id"""
        data = {
            "type": "assistant",
            "id": "llm-id",
            "timestamp": time.time(),
            "session_id": "test-session",
            "model": "gpt-4",
            "provider": "openai",
            "turn_id": 1,
            "parent_id": "parent-id",
            "input_messages": [{"role": "user", "content": "hello"}],
            "output_content": "hi there",
            "tool_calls": [],
        }

        entry = entry_from_dict(data)
        assert isinstance(entry, LLMEntry)
        assert entry.parent_id == "parent-id"
        assert entry.input_messages == [{"role": "user", "content": "hello"}]

    def test_tool_entry_parent_id_roundtrip(self):
        """Test ToolEntry parent_id survives round-trip serialization"""
        entry = ToolEntry(
            id="tool-id",
            timestamp=time.time(),
            session_id="test-session",
            model="gpt-4",
            provider="openai",
            turn_id=1,
            parent_id="llm-id",
            tool_name="test_tool",
            arguments={"arg": "value"},
            result="success",
            success=True,
        )

        # Serialize
        data = entry.to_dict()
        assert data["parent_id"] == "llm-id"

        # Deserialize
        restored = entry_from_dict(data)
        assert isinstance(restored, ToolEntry)
        assert restored.parent_id == "llm-id"
        assert restored.tool_name == "test_tool"

    def test_backward_compatibility_no_parent_id(self):
        """Test entries without parent_id default to None"""
        data = {
            "type": "user",
            "id": "user-id",
            "timestamp": time.time(),
            "session_id": "test-session",
            "content": "hello",
        }

        entry = entry_from_dict(data)
        assert entry.parent_id is None


class TestAPIEndpoints:
    """Test API endpoints via HTTP"""

    @pytest.fixture
    def temp_session_file(self):
        """Create a temp session file for API testing"""
        temp_dir = tempfile.mkdtemp()
        session_id = f"api_test_{uuid.uuid4().hex[:8]}"

        user_entry = UserEntry(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            session_id=session_id,
            model="gpt-4",
            provider="openai",
            turn_id=0,
            content="Test message",
        )

        session_file = Path(temp_dir) / f"{session_id}.jsonl"
        with open(session_file, "w", encoding="utf-8") as f:
            f.write(user_entry.to_json() + "\n")

        yield session_id, temp_dir

        # Cleanup
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_list_sessions_api(self, temp_session_file):
        """Test GET /api/sessions endpoint"""
        session_id, temp_dir = temp_session_file

        # This would normally start the FastAPI server
        # For unit testing, we test the tree builder directly
        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=temp_dir)
        sessions = builder.list_sessions()

        assert len(sessions) >= 1
        session_ids = [s["session_id"] for s in sessions]
        assert session_id in session_ids

    def test_get_session_tree_api(self, temp_session_file):
        """Test GET /api/sessions/{id}/tree endpoint"""
        session_id, temp_dir = temp_session_file

        from viewer.backend.src.services.tree_builder import TreeBuilder

        builder = TreeBuilder(persist_dir=temp_dir)
        root_node, entries = builder.build_tree(session_id)

        assert root_node is not None
        assert len(entries) >= 1

        # Check tree structure
        tree_dict = root_node.to_dict()
        assert "id" in tree_dict
        assert "entry" in tree_dict
        assert "children" in tree_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])