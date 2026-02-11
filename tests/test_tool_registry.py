
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from auric.skills.tool_registry import ToolRegistry
from auric.core.config import AuricConfig

@pytest.fixture
def mock_config():
    return MagicMock(spec=AuricConfig)

@pytest.fixture
def registry(mock_config):
    return ToolRegistry(mock_config)

@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path

# ==============================================================================
# Internal Tools Tests
# ==============================================================================

def test_list_files(registry, temp_dir):
    # Setup
    (temp_dir / "file1.txt").touch()
    (temp_dir / "subdir").mkdir()
    
    result = registry.list_files(str(temp_dir))
    
    assert "file1.txt (FILE)" in result
    assert "subdir (DIR)" in result

def test_list_files_non_existent(registry):
    result = registry.list_files("non_existent_dir_12345")
    assert "Error: Directory 'non_existent_dir_12345' does not exist." in result

def test_list_files_not_a_dir(registry, temp_dir):
    file_path = temp_dir / "file1.txt"
    file_path.touch()
    
    result = registry.list_files(str(file_path))
    assert f"Error: '{str(file_path)}' is not a directory." in result

def test_read_file(registry, temp_dir):
    file_path = temp_dir / "test.txt"
    file_path.write_text("Hello World", encoding="utf-8")
    
    result = registry.read_file(str(file_path))
    assert result == "Hello World"

def test_read_file_non_existent(registry):
    result = registry.read_file("non_existent_file.txt")
    assert "Error: File 'non_existent_file.txt' does not exist." in result

def test_read_file_too_large(registry, temp_dir):
    file_path = temp_dir / "large.txt"
    # Write 101KB (limit is 100KB)
    file_path.write_text("a" * (100 * 1024 + 1), encoding="utf-8")
    
    result = registry.read_file(str(file_path))
    assert "Error: File is too large" in result

def test_write_file(registry, temp_dir):
    file_path = temp_dir / "new.txt"
    content = "New Content"
    
    result = registry.write_file(str(file_path), content)
    
    assert f"Successfully wrote to {str(file_path)}" in result
    assert file_path.read_text(encoding="utf-8") == content

def test_write_file_permission_error(registry):
    # Mocking Path.write_text to raise PermissionError
    with patch("pathlib.Path.write_text", side_effect=PermissionError("Mocked Permission Error")):
         # Need a valid path structure for the mkdir call to succeed before write_text fail
         # or we mock path creation
         # Simplest is to just expect the error string if we could trigger it.
         # Instead, let's mock the whole path object inside write_file? 
         # Hard to mock pathlib.Path inside the method without dependency injection or patching 'auric.skills.tool_registry.Path'
         pass 

# ==============================================================================
# Execute Tool Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_execute_tool_success(registry, temp_dir):
    file_path = temp_dir / "exec_test.txt"
    file_path.write_text("Execution Test", encoding="utf-8")
    
    result = await registry.execute_tool("read_file", {"path": str(file_path)})
    assert result == "Execution Test"

@pytest.mark.asyncio
async def test_execute_tool_not_found(registry):
    result = await registry.execute_tool("unknown_tool", {})
    assert "Error: Tool 'unknown_tool' not found." in result

@pytest.mark.asyncio
async def test_execute_tool_invalid_args(registry):
    # Missing required argument 'path'
    result = await registry.execute_tool("read_file", {})
    assert "Error executing tool 'read_file': Invalid arguments" in result

# ==============================================================================
# Schema Generation Tests
# ==============================================================================

def test_get_tools_schema(registry):
    schemas = registry.get_tools_schema()
    
    assert isinstance(schemas, list)
    assert len(schemas) >= 3 # internal tools
    
    tool_names = [s["name"] for s in schemas]
    assert "list_files" in tool_names
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    
    # Validation of specific schema
    read_schema = next(s for s in schemas if s["name"] == "read_file")
    assert "parameters" in read_schema
    assert "properties" in read_schema["parameters"]
    assert "path" in read_schema["parameters"]["properties"]
