import os
import pytest
from auric.spells.tool_registry import ToolRegistry
from auric.core.config import AuricConfig

def test_os_aware_tool_registration():
    config = AuricConfig()
    registry = ToolRegistry(config)
    
    # Check that both are in internal tools dictionary (implementation exists)
    assert "execute_powershell" in registry._internal_tools
    assert "execute_bash" in registry._internal_tools
    
    # Check context filtering
    context = registry.get_internal_tools_context()
    if os.name == 'nt':
        assert "execute_powershell" in context
        assert "execute_bash" not in context
    else:
        assert "execute_powershell" not in context
        assert "execute_bash" in context

def test_os_aware_tool_schema():
    config = AuricConfig()
    registry = ToolRegistry(config)
    
    schemas = registry.get_tools_schema()
    tool_names = [s["function"]["name"] for s in schemas]
    
    if os.name == 'nt':
        assert "execute_powershell" in tool_names
        assert "execute_bash" not in tool_names
    else:
        assert "execute_powershell" not in tool_names
        assert "execute_bash" in tool_names

def test_tool_execution_restriction():
    config = AuricConfig()
    registry = ToolRegistry(config)
    
    if os.name == 'nt':
        # On Windows, bash should fail
        result = registry.execute_bash("ls")
        assert "Error: execute_bash is only available on Linux and macOS systems." in result
    else:
        # On Unix, powershell should fail
        result = registry.execute_powershell("Get-Process")
        assert "Error: execute_powershell is only available on Windows systems." in result
