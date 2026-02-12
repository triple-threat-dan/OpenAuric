"""
The Magic Circle: Safe Python Execution Sandbox.
"""

import ast
import asyncio
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import List, Optional, Set

from auric.core.config import AuricConfig

logger = logging.getLogger("auric.sandbox")

class SecurityViolationError(Exception):
    """Raised when code violates sandbox security rules."""
    pass

class SandboxManager:
    """
    Manages the isolated Python environment and executes code safely.
    """
    
    BLOCKED_MODULES = {"os", "sys", "subprocess", "shutil"}
    # Standard data science and utility stack
    SAFE_PACKAGES = ["pandas", "numpy", "requests", "beautifulsoup4"]
    
    def __init__(self, config: AuricConfig):
        self.config = config
        self.sandbox_dir = Path.home() / ".auric" / ".auric_sandbox"
        self.temp_dir = Path.home() / ".auric" / "temp"
        
        # Determine python executable path based on OS
        if sys.platform == "win32":
            self.python_exe = self.sandbox_dir / "Scripts" / "python.exe"
        else:
            self.python_exe = self.sandbox_dir / "bin" / "python"

    def _get_uv_path(self) -> str:
        """Locates the uv executable."""
        # 1. Check PATH
        uv_path = shutil.which("uv")
        if uv_path:
            return uv_path
            
        # 2. Check python package
        try:
            import uv
            return uv.find_uv_bin()
        except ImportError:
            pass
            
        raise RuntimeError("The 'uv' tool is not found in PATH or as a Python package. Please install uv.")


    async def ensure_environment(self) -> None:
        """
        Ensures the sandbox venv exists and has required packages.
        Uses 'uv' for fast environment management.
        """
        # Create directory structure if needed
        if not self.sandbox_dir.parent.exists():
            self.sandbox_dir.parent.mkdir(parents=True, exist_ok=True)

        if not self.sandbox_dir.exists():
            logger.info(f"Creating sandbox environment at {self.sandbox_dir}")
            try:
                uv_bin = self._get_uv_path()
                # Create venv using uv
                # We use subprocess directly to invoke uv
                process = await asyncio.create_subprocess_exec(
                    uv_bin, "venv", str(self.sandbox_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if process.returncode != 0:
                    logger.error(f"uv venv failed: {stderr.decode()}")
                    raise RuntimeError(f"Could not create sandbox: {stderr.decode()}")
                    
            except FileNotFoundError:
                raise RuntimeError("The 'uv' tool is not found in PATH. Please install uv.")
                
        # Install safe packages
        # Only run install if we suspect they might be missing? 
        # For now, running it every time ensures consistency, and uv is fast.
        logger.info("Ensuring safe packages in sandbox...")
        try:
            uv_bin = self._get_uv_path()
            # install packages using uv pip
            # uv pip install -p <python_path> <packages>
            cmd = [uv_bin, "pip", "install", "-p", str(self.python_exe)] + self.SAFE_PACKAGES
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"uv pip install failed: {stderr.decode()}")
                raise RuntimeError(f"Could not install sandbox packages: {stderr.decode()}")
            
        except Exception as e:
             logger.error(f"Failed to install packages: {e}")
             raise

        # Ensure temp dir exists for scripts
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def validate_code(self, code: str) -> None:
        """
        Static analysis to block dangerous imports.
        Raises SecurityViolationError if unsafe code is detected.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"Invalid Python code: {e}")

        # Get allowed imports from config
        allowed = set(self.config.sandbox.allowed_imports)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, allowed)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._check_import(node.module, allowed)

    def _check_import(self, module_name: str, allowed: Set[str]) -> None:
        """Helper to check if a module is blocked."""
        # Check base module name (e.g., 'os.path' -> 'os')
        base_module = module_name.split('.')[0]
        
        if base_module in self.BLOCKED_MODULES:
            if base_module not in allowed:
                raise SecurityViolationError(
                    f"Import of '{module_name}' is blocked by sandbox policy. "
                    f"Authorized modules: {allowed}"
                )

    async def run_python(self, code: str, timeout: int = 30) -> str:
        """
        Executes code in the sandbox.
        Returns captured stdout/stderr.
        """
        # 1. Validate
        self.validate_code(code)

        # 2. Persist
        script_id = uuid.uuid4()
        script_path = self.temp_dir / f"spell_{script_id}.py"
        
        try:
            # Write code to temp file
            script_path.write_text(code, encoding="utf-8")
            
            # 3. Execute
            # Using python executable from sandbox
            process = await asyncio.create_subprocess_exec(
                str(self.python_exe),
                str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                # Kill process on timeout
                try:
                    process.kill()
                    await process.wait() # Ensure it's reaped
                except Exception:
                    pass
                return f"TimeoutError: Code execution timed out after {timeout} seconds."
            
            output = stdout.decode().strip()
            error = stderr.decode().strip()
            
            if process.returncode != 0:
                # Return standardized error message
                return f"Execution failed (Exit Code {process.returncode}):\n{error}\n{output}"
            
            return output if output else (error if error else "Success (No Output)")

        except Exception as e:
            return f"System Error: {str(e)}"

        finally:
            # Cleanup temp file
            if script_path.exists():
                try:
                    script_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete temp script {script_path}: {e}")
