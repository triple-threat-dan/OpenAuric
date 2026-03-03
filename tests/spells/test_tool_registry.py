import pytest
from pathlib import Path
import json

from auric.spells.tool_registry import ToolRegistry
from auric.core.config import AuricConfig

def test_generate_function_schema_with_docstring_params():
    config = AuricConfig()
    registry = ToolRegistry(config)
    
    def mock_tool(directory: str, filter_pattern: str = "*"):
        """
        A mock tool for testing docstring parsing.
        
        Args:
            directory: The directory to search in.
            filter_pattern: The pattern to filter files by.
            
        Returns:
            A list of files.
        """
        pass
        
    schema = registry._generate_function_schema(mock_tool)
    
    assert schema["name"] == "mock_tool"
    assert schema["description"] == "A mock tool for testing docstring parsing."
    assert schema["parameters"]["properties"]["directory"]["description"] == "The directory to search in."
    assert schema["parameters"]["properties"]["filter_pattern"]["description"] == "The pattern to filter files by."
    assert "directory" in schema["parameters"]["required"]
    assert "filter_pattern" not in schema["parameters"]["required"]

def test_load_single_spell_multi_line_frontmatter(tmp_path):
    # Create a dummy SKILL.md with multi-line parameters_json
    skill_dir = tmp_path / "test-spell"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    
    content = """---
name: test-spell
description: A test spell with multi-line frontmatter.
parameters_json: {
  "type": "object",
  "properties": {
    "query": {"type": "string"}
  }
}
---
Instructions here.
"""
    skill_file.write_text(content, encoding="utf-8")
    
    config = AuricConfig()
    registry = ToolRegistry(config)
    registry.spells_dir = tmp_path # Point to our temp dir
    
    registry._load_single_spell(skill_file)
    
    assert "test-spell" in registry._spells
    spell = registry._spells["test-spell"]
    assert spell["name"] == "test-spell"
    assert spell["description"] == "A test spell with multi-line frontmatter."
    assert spell["parameters"]["type"] == "object"
    assert "query" in spell["parameters"]["properties"]
    assert spell["instructions"] == "Instructions here."

def test_get_internal_tools_context():
    config = AuricConfig()
    registry = ToolRegistry(config)
    
    context = registry.get_internal_tools_context()
    assert "## Internal Standard Tools" in context
    assert "read_file" in context
    assert "write_file" in context
    assert "execute_powershell" in context
    # Check if we have descriptions, not just names
    assert "Read the contents of a text file" in context

from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

@pytest.mark.asyncio
async def test_query_chat_history_no_audit_logger():
    config = AuricConfig()
    registry = ToolRegistry(config, audit_logger=None)
    
    result = await registry.query_chat_history("test")
    assert result == "Error: Audit Logger is not available."

@pytest.mark.asyncio
async def test_query_chat_history_no_matching_session():
    config = AuricConfig()
    mock_audit = AsyncMock()
    mock_audit.get_sessions.return_value = [
        {"session_id": "1", "name": "General Chat"},
        {"session_id": "2", "name": "Alice DM"}
    ]
    registry = ToolRegistry(config, audit_logger=mock_audit)
    
    result = await registry.query_chat_history("Bob")
    assert "No session found matching 'Bob'" in result
    assert "General Chat" in result
    assert "Alice DM" in result

@pytest.mark.asyncio
async def test_query_chat_history_no_messages():
    config = AuricConfig()
    mock_audit = AsyncMock()
    mock_audit.get_sessions.return_value = [{"session_id": "123", "name": "TargetUser"}]
    mock_audit.get_chat_history.return_value = []
    
    registry = ToolRegistry(config, audit_logger=mock_audit)
    
    result = await registry.query_chat_history("targetuser")
    assert result == "No messages found in session 'targetuser'."
    mock_audit.get_chat_history.assert_called_once_with(limit=50, session_id="123")

@pytest.mark.asyncio
async def test_query_chat_history_success():
    config = AuricConfig()
    mock_audit = AsyncMock()
    mock_audit.get_sessions.return_value = [{"session_id": "123", "name": "TargetUser"}]
    
    mock_msg1 = MagicMock()
    mock_msg1.timestamp = datetime(2023, 1, 1, 12, 0)
    mock_msg1.role = "USER"
    mock_msg1.content = "Hello bot"
    
    mock_msg2 = MagicMock()
    mock_msg2.timestamp = datetime(2023, 1, 1, 12, 1)
    mock_msg2.role = "AGENT"
    mock_msg2.content = "Hello user"
    
    mock_audit.get_chat_history.return_value = [mock_msg1, mock_msg2]
    
    registry = ToolRegistry(config, audit_logger=mock_audit)
    
    result = await registry.query_chat_history("targetuser", limit=10)
    
    assert "--- History for targetuser ---" in result
    assert "[2023-01-01 12:00] User: Hello bot" in result
    assert "[2023-01-01 12:01] Agent: Hello user" in result
    mock_audit.get_chat_history.assert_called_once_with(limit=10, session_id="123")

@pytest.mark.asyncio
async def test_query_chat_history_exception():
    config = AuricConfig()
    mock_audit = AsyncMock()
    mock_audit.get_sessions.side_effect = Exception("DB Connection Failed")
    
    registry = ToolRegistry(config, audit_logger=mock_audit)
    
    result = await registry.query_chat_history("test")
    assert "Error querying chat history: DB Connection Failed" in result
