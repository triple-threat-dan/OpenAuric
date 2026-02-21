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
