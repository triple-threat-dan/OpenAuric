"""
Tool Registry and Gateway Manager for OpenAuric.

This module implements the ToolRegistry, which acts as the central hub for
tool discovery, execution, and schema generation. It manages both internal
standard library tools (filesystem operations) and external MCP servers.
"""

import logging
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Callable, Optional, Union
import inspect
import subprocess

from auric.core.config import AuricConfig, AURIC_ROOT
from auric.spells.sandbox import SandboxManager

logger = logging.getLogger("auric.spells")

class ToolRegistry:
    """
    Registry for managing and executing tools available to the agent.
    Acts as an MCP Client/Host, bundling internal tools and connecting to external ones.
    """

    def __init__(self, config: AuricConfig):
        self.config = config
        self._internal_tools: Dict[str, Callable] = {}
        self._spells: Dict[str, Dict[str, Any]] = {} # name -> spell_data
        
        # Register internal standard library tools
        self._register_internal_tool(self.list_files)
        self._register_internal_tool(self.read_file)
        self._register_internal_tool(self.write_file)
        self._register_internal_tool(self.execute_powershell)
        self._register_internal_tool(self.run_python)
        
        # Initialize Sandbox
        self.sandbox = SandboxManager(config)

        # Load Spells from Grimoire
        self.spells_dir = AURIC_ROOT / "grimoire"
        self.load_spells()

    def _register_internal_tool(self, func: Callable):
        """Registers a python function as an internal tool."""
        self._internal_tools[func.__name__] = func

    def load_spells(self):
        """Scans the spells directory and loads valid SKILL.md files."""
        self._spells = {}
        if not self.spells_dir.exists():
            try:
                self.spells_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create spells directory: {e}")
                return

        for item in self.spells_dir.iterdir():
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    self._load_single_spell(skill_file)
        
        logger.info(f"Loaded {len(self._spells)} spells.")
        self._generate_spells_index()

    def _load_single_spell(self, path: Path):
        """Parses a single SKILL.md and registers the spell."""
        try:
            content = path.read_text(encoding="utf-8")
            # Simple YAML frontmatter parser
            if content.startswith("---"):
                end_frontmatter = content.find("---", 3)
                if end_frontmatter != -1:
                    frontmatter_str = content[3:end_frontmatter]
                    
                    meta = {}
                    for line in frontmatter_str.splitlines():
                        if ":" in line:
                            key, val = line.split(":", 1)
                            meta[key.strip()] = val.strip()
                    
                    if "name" in meta:
                        name = meta["name"]
                        instructions = content[end_frontmatter+3:].strip()
                        
                        spell_data = {
                            "name": name,
                            "description": meta.get("description", "No description"),
                            "path": path.parent,
                            "instructions": instructions,
                            "script": self._find_script(path.parent)
                        }
                        self._spells[name] = spell_data
                        logger.debug(f"Loaded spell: {name}")
        except Exception as e:
            logger.error(f"Failed to load spell from {path}: {e}")

    def _find_script(self, spell_dir: Path) -> Optional[Path]:
        """Looks for executable scripts in the spell folder."""
        scripts_dir = spell_dir / "scripts"
        if scripts_dir.exists() and scripts_dir.is_dir():
            # Priority: run.py, run.ps1, run.sh
            for name in ["run.py", "run.ps1", "run.sh"]:
                script = scripts_dir / name
                if script.exists():
                    return script
        return None

    def _generate_spells_index(self):
        """Generates the SPELLS.md file for system prompt inclusion."""
        index_path = self.spells_dir.parent / "SPELLS.md"
        try:
            lines = ["# Available Spells", ""]
            for name, data in self._spells.items():
                lines.append(f"## {name}")
                lines.append(f"**Description**: {data['description']}")
                lines.append(f"**Format**: To use, output a tool call for `{name}`.")
                if data["script"]:
                    lines.append("**Type**: Executable (Auto-runs script)")
                else:
                    lines.append("**Type**: Instruction (Returns procedure)")
                lines.append("")
            
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write SPELLS.md: {e}")

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
                # Filter out hidden files/dirs starting with .
                if item.name.startswith('.'):
                    continue
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

    @staticmethod
    def execute_powershell(command: str) -> str:
        """
        Executes a PowerShell command securely on the host system.
        
        Args:
            command: The PowerShell command to execute.
            
        Returns:
            The stdout output or error message.
        """
        try:
            # We use powershell -Command "..."
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
            
            # Run synchonously because internal tools in this registry design are currently sync/async agnostic 
            # (execute_tool handles both), but subprocess.run is blocking. 
            # Ideally this should be async if high concurrency, but for an agent tool it's fine.
            # RLM calls tools sequentially anyway.
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return f"Error (Exit Code {result.returncode}):\n{result.stderr}\n{result.stdout}"
                
        except Exception as e:
            return f"Error executing PowerShell: {str(e)}"

    async def run_python(self, code: str) -> str:
        """
        Executes Python code in a secure, isolated sandbox.
        
        Args:
            code: The Python code to execute.
            
        Returns:
            The standard output or error message from the execution.
        """
        try:
            # Lazily ensure environment exists (async)
            await self.sandbox.ensure_environment()
            return await self.sandbox.run_python(code)
        except Exception as e:
            return f"Error executing Python code: {str(e)}"

    # ==========================================================================
    # Registry Operations
    # ==========================================================================

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """
        Executes a tool by name (Internal or Spell).

        Args:
            name: The name of the tool/spell.
            arguments: The arguments to pass.

        Returns:
            Execution result.
        """
        logger.info(f"Executing tool: {name} with args: {arguments}")
        
        # 1. Internal Tools
        if name in self._internal_tools:
            try:
                func = self._internal_tools[name]
                # Check if async
                if inspect.iscoroutinefunction(func):
                    result = await func(**arguments)
                else:
                    result = func(**arguments)
                return str(result)
            except TypeError as e:
                 return f"Error executing tool '{name}': Invalid arguments - {str(e)}"
            except Exception as e:
                logger.error(f"Tool execution failed: {e}", exc_info=True)
                return f"Error executing tool '{name}': {str(e)}"

        # 2. Spells
        if name in self._spells:
            return await self._execute_spell(name, arguments)
        
        return f"Error: Tool or Spell '{name}' not found."

    async def _execute_spell(self, name: str, args: Dict[str, Any]) -> str:
        """Executes a spell (runs script or returns instructions)."""
        spell = self._spells[name]
        script_path = spell["script"]

        if script_path:
            # Execute Script
            CMD = []
            
            # Determine interpreter
            if script_path.suffix == ".py":
                CMD = ["python", str(script_path)]
            elif script_path.suffix == ".ps1":
                CMD = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
            elif script_path.suffix == ".sh":
                CMD = ["bash", str(script_path)]
            else:
                 return f"Error: Unknown script type for {script_path}"

            # Pass args as a SINGLE JSON string argument
            import json
            args_json = json.dumps(args)
            CMD.append(args_json)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *CMD,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                
                output = stdout.decode().strip()
                error = stderr.decode().strip()
                
                if proc.returncode != 0:
                    return f"Spell '{name}' failed (Exit Code {proc.returncode}):\n{error}\n{output}"
                
                return output if output else f"Spell '{name}' executed successfully."
            except Exception as e:
                 return f"Error launching spell script: {e}"
        else:
            # Instruction-only Spell
            # Instruction-only Spell
            return f"## Spell Instructions: {name}\n\n{spell['instructions']}\n\n## Spell Context\n- **Path**: {spell['path']}\n\n[End of Spell]"

        return schemas
    
    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Generates schemas for Internal Tools + Spells.
        Output format compatible with OpenAI/Litellm:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}
            }
        }
        """
        schemas = []
        
        # Internal
        for name, func in self._internal_tools.items():
            func_schema = self._generate_function_schema(func)
            schemas.append({
                "type": "function",
                "function": func_schema
            })
            
        # Spells
        for name, data in self._spells.items():
            # Basic schema for spells
            spell_schema = {
                "name": name,
                "description": data["description"],
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instructions": {
                             "type": "string",
                             "description": "Optional instructions for the spell."
                        }
                    },
                    "additionalProperties": True 
                }
            }
            schemas.append({
                "type": "function",
                "function": spell_schema
            })
        
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
                "description": param_name
            }
            
            if param.default == inspect.Parameter.empty:
                parameters["required"].append(param_name)
                
        return {
            "name": name,
            "description": doc,
            "parameters": parameters
        }
