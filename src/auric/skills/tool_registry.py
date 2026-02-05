"""
Tool Registry and Gateway Manager for OpenAuric.

This module implements the ToolRegistry, which acts as the central hub for
tool discovery, execution, and schema generation. It manages both internal
standard library tools (filesystem operations) and external MCP servers.
"""

import logging
import json
from pathlib import Path
from typing import Dict, List, Any, Callable, Optional, Union
import inspect

from auric.core.config import AuricConfig

logger = logging.getLogger("auric.skills")

class ToolRegistry:
    """
    Registry for managing and executing tools available to the agent.
    Acts as an MCP Client/Host, bundling internal tools and connecting to external ones.
    """

    def __init__(self, config: AuricConfig):
        self.config = config
        self._internal_tools: Dict[str, Callable] = {}
        # self._mcp_clients = [] # Placeholder for external MCP clients

        # Register internal standard library tools
        self._register_internal_tool(self.list_files)
        self._register_internal_tool(self.read_file)
        self._register_internal_tool(self.write_file)

    def _register_internal_tool(self, func: Callable):
        """Registers a python function as an internal tool."""
        self._internal_tools[func.__name__] = func

    # ==========================================================================
    # Internal Standard Library Tools
    # ==========================================================================

    @staticmethod
    def list_files(directory: str) -> str:
        """
        List files and directories in the specified path.

        Args:
            directory: The directory path to list.

        Returns:
            A formatted string listing the contents, or an error message.
        """
        try:
            path = Path(directory)
            if not path.exists():
                return f"Error: Directory '{directory}' does not exist."
            if not path.is_dir():
                return f"Error: '{directory}' is not a directory."

            items = []
            for item in path.iterdir():
                type_label = "(DIR)" if item.is_dir() else "(FILE)"
                items.append(f"- {item.name} {type_label}")
            
            if not items:
                return "(Empty directory)"
            
            return "\n".join(items)
        except PermissionError:
            return f"Error: Permission denied accessing '{directory}'."
        except Exception as e:
            return f"Error listing files: {str(e)}"

    @staticmethod
    def read_file(path: str) -> str:
        """
        Read the contents of a text file.

        Args:
            path: The path to the file to read.

        Returns:
            The content of the file, or an error message.
        """
        MAX_SIZE = 100 * 1024  # 100KB limit
        try:
            file_path = Path(path)
            if not file_path.exists():
                return f"Error: File '{path}' does not exist."
            if not file_path.is_file():
                return f"Error: '{path}' is not a file."
            
            stat = file_path.stat()
            if stat.st_size > MAX_SIZE:
                 return f"Error: File is too large ({stat.st_size} bytes). Max size is {MAX_SIZE} bytes."

            return file_path.read_text(encoding='utf-8', errors='replace')
        except PermissionError:
             return f"Error: Permission denied reading '{path}'."
        except Exception as e:
            return f"Error reading file: {str(e)}"

    @staticmethod
    def write_file(path: str, content: str) -> str:
        """
        Write content to a file. Overwrites existing content.

        Args:
            path: The path to the file to write.
            content: The content to write.

        Returns:
            Success message or error message.
        """
        try:
            file_path = Path(path)
            # Ensure parent directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            file_path.write_text(content, encoding='utf-8')
            return f"Successfully wrote to {path}"
        except PermissionError:
             return f"Error: Permission denied writing to '{path}'."
        except Exception as e:
            return f"Error writing file: {str(e)}"

    # ==========================================================================
    # Registry Operations
    # ==========================================================================

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        Executes a tool by name with the given arguments.

        Args:
            name: The name of the tool to execute.
            arguments: The arguments to pass to the tool.

        Returns:
            The output of the tool execution as a string.
        """
        logger.info(f"Executing tool: {name} with args: {arguments}")
        
        if name in self._internal_tools:
            try:
                func = self._internal_tools[name]
                # Internal tools are synchronous for now, but we wrap them if needed
                # In a real async loop, we might want to run file I/O in an executor
                # but for simplicity/safety we run direct call here as they are fast enough for now
                # or we can use asyncio.to_thread if they become blocking.
                
                # Check for unexpected arguments if strictness is required, 
                # but Python handles kwargs matching reasonably well or we can inspect.
                
                result = func(**arguments)
                return str(result)
            except TypeError as e:
                 return f"Error executing tool '{name}': Invalid arguments - {str(e)}"
            except Exception as e:
                logger.error(f"Tool execution failed: {e}", exc_info=True)
                return f"Error executing tool '{name}': {str(e)}"
        
        # TODO: Check external MCP clients here
        
        return f"Error: Tool '{name}' not found."

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Generates the JSON Schema for all registered tools (internal + external).
        
        Returns:
            A list of tool definitions compatible with OpenAI/Gemini function calling.
        """
        schemas = []
        
        # Generate schemas for internal tools
        for name, func in self._internal_tools.items():
            schema = self._generate_function_schema(func)
            schemas.append(schema)
            
        # TODO: Add schemas from external MCP clients
        
        return schemas

    def _generate_function_schema(self, func: Callable) -> Dict[str, Any]:
        """
        Helper to generate OpenAI-compatible schema from a Python function.
        Uses introspection of type hints and docstrings.
        """
        name = func.__name__
        doc = inspect.getdoc(func) or "No description available."
        sig = inspect.signature(func)
        
        parameters = {
            "type": "object",
            "properties": {},
            "required": []
        }
        
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
                
            param_type = "string" # Default to string
            if param.annotation == int:
                param_type = "integer"
            elif param.annotation == float:
                param_type = "number"
            elif param.annotation == bool:
                param_type = "boolean"
            elif param.annotation == dict:
                param_type = "object"
            elif param.annotation == list:
                param_type = "array"
            
            parameters["properties"][param_name] = {
                "type": param_type,
                "description": param_name # Could parse docstring for per-param desc
            }
            
            if param.default == inspect.Parameter.empty:
                parameters["required"].append(param_name)
                
        return {
            "name": name,
            "description": doc,
            "parameters": parameters
        }
