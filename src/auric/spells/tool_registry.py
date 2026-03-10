"""
Tool Registry and Gateway Manager for OpenAuric.

Central hub for tool discovery, execution, and schema generation.
Manages internal standard library tools and external spells.
"""

import asyncio
import inspect
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from auric.core.config import AURIC_ROOT, AuricConfig
from auric.spells.sandbox import SandboxManager

logger = logging.getLogger("auric.spells")

# --- Constants & Compiled Regex ---
IS_WINDOWS = os.name == 'nt'
FRONTMATTER_RE = re.compile(r"^---$", re.MULTILINE)
META_RE = re.compile(r"^(\w+):\s*(.*?)(?=\n\w+:|\Z)", re.DOTALL | re.MULTILINE)
PARAM_DESC_RE = re.compile(r'^\s*(\w+)(?:\s*\(.*?\))?:\s*(.*)', re.MULTILINE)
ARGS_SECTION_RE = re.compile(r'\n\s*(?:Args|Parameters):\s*\n', re.IGNORECASE)
PENDING_TASK_RE = re.compile(r"^\s*-\s+.*", re.MULTILINE)

class ToolRegistry:
    """Registry for managing and executing tools available to the agent."""

    def __init__(self, config: AuricConfig, librarian=None, audit_logger=None, session_router=None):
        self.config = config
        self.librarian = librarian
        self.audit_logger = audit_logger
        self.session_router = session_router
        self._internal_tools: Dict[str, Callable] = {}
        self._spells: Dict[str, Dict[str, Any]] = {}
        
        # Performance caches
        self._cached_schema: List[Dict[str, Any]] = []
        self._cached_internal_context: Optional[str] = None
        self._cached_spells_context: Optional[str] = None

        # Register core tools
        self._register_internal_tool(self.list_files)
        self._register_internal_tool(self.read_file)
        self._register_internal_tool(self.write_file)
        self._register_internal_tool(self.append_file)
        self._register_internal_tool(self.execute_powershell)
        self._register_internal_tool(self.execute_bash)
        self._register_internal_tool(self.run_python)
        
        if self.librarian:
            self._register_internal_tool(self.memory_search)
        if self.audit_logger:
            self._register_internal_tool(self.query_chat_history)
        
        self.sandbox = SandboxManager(config)
        self.spells_dir = AURIC_ROOT / "grimoire"
        self.load_spells()

    def _register_internal_tool(self, func: Callable):
        self._internal_tools[func.__name__] = func

    def load_spells(self):
        """Scans the grimoire and loads valid SKILL.md files."""
        self._spells = {}
        self._cached_spells_context = None
        self._cached_schema = [] # Invalidate schema cache
        
        if not self.spells_dir.exists():
            self.spells_dir.mkdir(parents=True, exist_ok=True)

        for item in self.spells_dir.iterdir():
            if item.is_dir() and (skill_file := item / "SKILL.md").exists():
                self._load_single_spell(skill_file)
        
        logger.debug(f"Loaded {len(self._spells)} spells.")

    def _load_single_spell(self, path: Path):
        try:
            content = path.read_text(encoding="utf-8")
            parts = FRONTMATTER_RE.split(content, maxsplit=2)
            
            if len(parts) >= 3:
                meta = {m.group(1): m.group(2).strip() for m in META_RE.finditer(parts[1])}
                
                if name := meta.get("name"):
                    params = {}
                    if p_json := meta.get("parameters_json"):
                        try:
                            params = json.loads(p_json)
                        except json.JSONDecodeError:
                            logger.error(f"Invalid parameters_json for {name}")

                    self._spells[name] = {
                        "name": name,
                        "description": meta.get("description", "No description"),
                        "path": path.parent,
                        "instructions": parts[2].strip(),
                        "script": self._find_script(path.parent),
                        "parameters": params
                    }
        except Exception as e:
            logger.error(f"Failed to load spell from {path}: {e}")

    def _find_script(self, spell_dir: Path) -> Optional[Path]:
        scripts_dir = spell_dir / "scripts"
        if scripts_dir.is_dir():
            for name in ["run.py", "run.ps1", "run.sh"]:
                if (script := scripts_dir / name).exists():
                    return script
        return None

    def get_internal_tools_context(self) -> str:
        """Returns summarized internal tools documentation."""
        if self._cached_internal_context:
            return self._cached_internal_context

        if not self._internal_tools:
            return ""

        lines = ["## Internal Standard Tools", ""]
        for name, func in self._internal_tools.items():
            if (name == "execute_powershell" and not IS_WINDOWS) or \
               (name == "execute_bash" and IS_WINDOWS):
                continue
                
            doc = inspect.getdoc(func) or "No description available."
            summary = doc.split("\n\n")[0].strip()
            lines.append(f"- **{name}**: {summary}")
        
        lines.append("- **CRITICAL**: Use memory_search to query your Grimoire/Memories for context. PREFER this over direct file reads.")
        lines.append("- Use read_file only for specific file contents or when memory_search yields no results.")

        self._cached_internal_context = "\n".join(lines)
        return self._cached_internal_context

    def get_spells_context(self) -> str:
        """Returns summarized spells documentation."""
        if self._cached_spells_context:
            return self._cached_spells_context

        if not self._spells:
            return ""

        lines = ["## Available Spells", "Use `spell_crafter` to initiate a spell.", ""]
        cwd = Path.cwd()
        
        for name, data in self._spells.items():
            try:
                rel_path = data['path'].relative_to(cwd)
            except ValueError:
                rel_path = data['path']

            lines.append(f"### {name}")
            lines.append(f"**Path**: {rel_path}")
            lines.append(f"**Description**: {data['description']}") 
            if params := data.get("parameters"):
                lines.append(f"**Parameters**: ```json\n{json.dumps(params)}\n```")
            lines.append("")
            
        self._cached_spells_context = "\n".join(lines)
        return self._cached_spells_context

    # --- Standard Library Tools ---

    @staticmethod
    def list_files(directory: str) -> str:
        """List files and directories in the specified path."""
        try:
            path = Path(directory).expanduser().resolve()
            if not path.is_dir():
                return f"Error: '{directory}' is not a directory or does not exist."

            items = [f"- {item.name} {'(DIR)' if item.is_dir() else '(FILE)'}" 
                     for item in path.iterdir() if not item.name.startswith('.')]
            return "\n".join(items) if items else "(Empty directory)"
        except Exception as e:
            return f"Error listing files: {e}"

    @staticmethod
    def read_file(path: str) -> str:
        """Read the contents of a text file (max 100KB)."""
        try:
            p = Path(path).expanduser().resolve()
            if not p.is_file():
                return f"Error: '{path}' is not a file or does not exist."
            if p.stat().st_size > 102400:
                 return f"Error: File '{path}' exceeds 100KB limit."
            return p.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            return f"Error reading file: {e}"

    @staticmethod
    def _sanitize_content(path: str, content: str) -> str:
        if str(path).lower().endswith(('.md', '.txt', '.rst')) and isinstance(content, str):
            content = re.sub(r'(?<!\\)\\n', '\n', content)
        return content

    @staticmethod
    def write_file(path: str, content: str) -> str:
        """Write content to a file, overwriting existing data."""
        try:
            p = Path(path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(ToolRegistry._sanitize_content(path, content), encoding='utf-8')
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    @staticmethod
    def append_file(path: str, content: str) -> str:
        """Append content to a file."""
        try:
            p = Path(path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(ToolRegistry._sanitize_content(path, content))
            return f"Successfully appended to {path}"
        except Exception as e:
            return f"Error appending file: {e}"

    @staticmethod
    async def execute_powershell(command: str) -> str:
        """Executes a PowerShell command (Windows only)."""
        if not IS_WINDOWS:
            return "Error: execute_powershell is Windows-only."
        return await ToolRegistry._run_shell(["powershell", "-NoProfile", "-NonInteractive", "-Command", command])

    @staticmethod
    async def execute_bash(command: str) -> str:
        """Executes a Bash command (Linux/macOS only)."""
        if IS_WINDOWS:
            return "Error: execute_bash is Linux/macOS only."
        return await ToolRegistry._run_shell(["/bin/bash", "-c", command])

    @staticmethod
    async def _run_shell(cmd: List[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode().strip()
            if proc.returncode == 0:
                return output
            return f"Error (Code {proc.returncode}):\n{stderr.decode().strip()}\n{output}"
        except Exception as e:
            return f"Execution error: {e}"

    async def run_python(self, code: str) -> str:
        """Executes Python code in a secure sandbox."""
        try:
            await self.sandbox.ensure_environment()
            return await self.sandbox.run_python(code)
        except Exception as e:
            return f"Python execution error: {e}"

    def memory_search(self, query: str) -> str:
        """Search the agent's Grimoire for relevant memories."""
        if not self.librarian:
            return "Error: Librarian not available."
        results = self.librarian.search(query)
        if not results:
            return "No relevant memories found."
        return "\n".join([f"--- from {res['metadata'].get('filename', 'Unknown')} (dist: {res.get('distance', 0):.4f}) ---\n{res['content']}\n" for res in results])

    async def query_chat_history(self, target: str, limit: int = 50) -> str:
        """Queries the chat history of a specific session by target name."""
        if not self.audit_logger:
            return "Error: Audit Logger is not available."
        try:
            sessions = await self.audit_logger.get_sessions()
            target_l = target.lower()
            sid = next((s["session_id"] for s in sessions if target_l in s.get("name", "").lower()), None)
            
            if not sid:
                available = ", ".join([s.get("name", "Unknown") for s in sessions[:10]])
                return f"No session found matching '{target}'. Available: {available}"
                
            history = await self.audit_logger.get_chat_history(limit=limit, session_id=sid)
            if not history:
                return f"No messages found in session '{target}'."
                
            lines = [f"--- History for {target} ---"]
            for msg in history:
                ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
                lines.append(f"[{ts}] {msg.role.capitalize()}: {msg.content}")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"History query failed for {target}: {e}")
            return f"Error querying chat history: {e}"

    # --- Registry Operations ---

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Executes a tool or spell by name."""
        logger.info(f"Executing: {name}")
        
        from auric.core.system_logger import SystemLogger
        sl = SystemLogger.get_instance()

        if func := self._internal_tools.get(name):
            try:
                res = await func(**arguments) if inspect.iscoroutinefunction(func) else func(**arguments)
                sl.log("TOOL_EXECUTION", {"name": name, "args": arguments, "result": str(res)[:1000]})
                return str(res)
            except Exception as e:
                logger.error(f"Tool {name} failed: {e}", exc_info=True)
                return f"Error: {e}"

        if name in self._spells:
            res = await self._execute_spell(name, arguments)
            sl.log("SPELL_EXECUTION", {"name": name, "args": arguments, "result": str(res)[:1000]})
            return res
        
        return f"Error: Tool '{name}' not found."

    async def _execute_spell(self, name: str, args: Dict[str, Any]) -> str:
        spell = self._spells[name]
        script = spell["script"]

        if not script:
            return f"## Spell Instructions: {name}\n\n{spell['instructions']}\n\n[End of Spell]"

        cmd = []
        if script.suffix == ".py": cmd = ["python", str(script)]
        elif script.suffix == ".ps1": cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        elif script.suffix == ".sh": cmd = ["bash", str(script)]
        else: return f"Error: Unknown script type {script}"

        cmd.append(json.dumps(args))
        env = os.environ.copy()
        # Inject known provider keys if available
        if self.config.keys.brave: env["BRAVE_SEARCH_API_KEY"] = self.config.keys.brave

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode().strip() or f"Spell '{name}' successful."
            return f"Spell '{name}' failed ({proc.returncode}):\n{stderr.decode().strip()}\n{stdout.decode().strip()}"
        except Exception as e:
             return f"Spell launch error: {e}"

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Generates OpenAI-compatible schemas for all tools."""
        if self._cached_schema:
            return self._cached_schema

        schemas = []
        for name, func in self._internal_tools.items():
            if (name == "execute_powershell" and not IS_WINDOWS) or \
               (name == "execute_bash" and IS_WINDOWS):
                continue
            schemas.append({"type": "function", "function": self._generate_function_schema(func)})
            
        for name, data in self._spells.items():
            params = data.get("parameters") or {
                "type": "object",
                "properties": {"instructions": {"type": "string"}},
                "additionalProperties": True 
            }
            schemas.append({
                "type": "function",
                "function": {"name": name, "description": data["description"], "parameters": params}
            })
        
        self._cached_schema = schemas
        return schemas

    def _generate_function_schema(self, func: Callable) -> Dict[str, Any]:
        """Introspects a Python function to generate an API schema."""
        doc = inspect.getdoc(func) or "No description."
        parts = ARGS_SECTION_RE.split(doc, maxsplit=1)
        
        p_desc = {}
        if len(parts) > 1:
            p_desc = {m.group(1): m.group(2).strip() for m in PARAM_DESC_RE.finditer(parts[1])}

        sig = inspect.signature(func)
        props = {}
        req = []
        
        type_map = {int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}
        
        for p_name, p in sig.parameters.items():
            if p_name == 'self': continue
            props[p_name] = {
                "type": type_map.get(p.annotation, "string"),
                "description": p_desc.get(p_name, p_name)
            }
            if p.default == inspect.Parameter.empty: req.append(p_name)
                
        return {"name": func.__name__, "description": parts[0].strip(), "parameters": {"type": "object", "properties": props, "required": req}}
