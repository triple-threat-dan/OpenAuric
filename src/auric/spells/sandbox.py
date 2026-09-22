"""
The Magic Circle: Safe Python Execution Sandbox for OpenAuric.

Provides an isolated virtual environment for executing arbitrary Python code
with static analysis to block dangerous operations.
"""

import ast
import asyncio
import logging
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional, Set

from auric.core.config import AuricConfig

logger = logging.getLogger("auric.sandbox")

# Constants for sandbox policy
BLOCKED_MODULES = frozenset({"os", "sys", "subprocess", "shutil"})
SAFE_PACKAGES = ("pandas", "numpy", "requests", "beautifulsoup4")

class SecurityViolationError(Exception):
    """Raised when code violates sandbox security rules."""

class SandboxManager:
    """
    Manages the isolated Python environment and executes code safely.
    Uses 'uv' for high-performance virtual environment management.
    """
    
    def __init__(self, config: AuricConfig):
        self.config = config
        self.sandbox_dir = Path.home() / ".auric" / ".auric_sandbox"
        self.temp_dir = Path.home() / ".auric" / "temp"
        self._uv_path: Optional[str] = None
        self._ready = False
        
        # Determine platform-specific executable path
        bin_name = "Scripts" if sys.platform == "win32" else "bin"
        exe_name = "python.exe" if sys.platform == "win32" else "python"
        self.python_exe = self.sandbox_dir / bin_name / exe_name

    def _get_uv_path(self) -> str:
        """Locates and caches the uv executable."""
        if self._uv_path:
            return self._uv_path

        uv_path = shutil.which("uv")
        if not uv_path:
            try:
                import uv
                uv_path = uv.find_uv_bin()
            except (ImportError, AttributeError):
                pass
            
        if not uv_path:
            raise RuntimeError("The 'uv' tool is not found. Please install uv.")
            
        self._uv_path = uv_path
        return uv_path

    async def _exec(self, *args: str, cwd: Optional[Path] = None) -> tuple[int, str, str]:
        """Helper to run a process and return returncode, stdout, stderr."""
        kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if cwd:
            kwargs["cwd"] = str(cwd)
            
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode().strip(), stderr.decode().strip()

    async def ensure_environment(self) -> None:
        """Ensures the sandbox exists and is ready. Short-circuits if already initialized."""
        if self._ready:
            return

        self.sandbox_dir.parent.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Validate existing venv
        if self.sandbox_dir.exists() and self.python_exe.exists():
            try:
                rc, _, _ = await self._exec(str(self.python_exe), "--version")
                if rc != 0:
                    logger.warning("Sandbox Python is broken. Recreating...")
                    shutil.rmtree(self.sandbox_dir, ignore_errors=True)
            except Exception:
                shutil.rmtree(self.sandbox_dir, ignore_errors=True)

        try:
            # Create venv if missing
            if not self.sandbox_dir.exists():
                logger.info(f"Creating sandbox environment at {self.sandbox_dir}")
                rc, _, stderr = await self._exec(self._get_uv_path(), "venv", str(self.sandbox_dir))
                if rc != 0:
                    raise RuntimeError(f"Could not create sandbox: {stderr}")

            # Sync packages
            logger.info("Ensuring safe packages in sandbox...")
            cmd = [self._get_uv_path(), "pip", "install", "-p", str(self.python_exe), *SAFE_PACKAGES]
            rc, _, stderr = await self._exec(*cmd)
            if rc != 0:
                raise RuntimeError(f"Could not install sandbox packages: {stderr}")

        except FileNotFoundError:
            raise RuntimeError("The 'uv' tool is not found. Please install uv.")

        self._ready = True

    def validate_code(self, code: str) -> None:
        """Performs static analysis to enforce security policy."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python code: {e}")

        allowed = set(self.config.sandbox.allowed_imports)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, allowed)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self._check_import(node.module, allowed)

    def _check_import(self, module_name: str, allowed: Set[str]) -> None:
        base_module = module_name.split('.')[0]
        if base_module in BLOCKED_MODULES and base_module not in allowed:
            raise SecurityViolationError(
                f"Import of '{module_name}' is blocked by sandbox policy. "
                f"Authorized modules: {allowed}"
            )

    async def run_python(self, code: str, timeout: int = 30) -> str:
        """Executes code within the sandbox and returns output."""
        self.validate_code(code)

        script_path = self.temp_dir / f"spell_{uuid.uuid4()}.py"
        
        try:
            script_path.write_text(code, encoding="utf-8")
            
            process = await asyncio.create_subprocess_exec(
                str(self.python_exe),
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                return f"TimeoutError: Code execution timed out after {timeout} seconds."
            
            output = stdout.decode(errors="replace").strip()
            error = stderr.decode(errors="replace").strip()
            
            if process.returncode != 0:
                return f"Execution failed (Exit Code {process.returncode}):\n{error}\n{output}".strip()
            
            return output or error or "Success (No Output)"

        except Exception as e:
            return f"System Error: {e}"

        finally:
            if script_path.exists():
                try:
                    script_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete temp script {script_path}: {e}")
