
import asyncio
import sys
from pathlib import Path
# Ensure src is in python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, mock_open

from auric.core.config import AuricConfig, SandboxConfig
from auric.skills.sandbox import SandboxManager, SecurityViolationError

# Fixtures

@pytest.fixture
def mock_config():
    return AuricConfig(sandbox=SandboxConfig())

@pytest.fixture
def sandbox(mock_config):
    return SandboxManager(mock_config)

# Tests

def test_init_paths(sandbox):
    """Test that paths are set up correctly based on platform."""
    assert sandbox.sandbox_dir.name == ".auric_sandbox"
    assert sandbox.temp_dir.name == "temp"
    
    if sys.platform == "win32":
        assert sandbox.python_exe.name == "python.exe"
        assert "Scripts" in str(sandbox.python_exe)
    else:
        assert sandbox.python_exe.name == "python"
        assert "bin" in str(sandbox.python_exe)

def test_validate_code_safe(sandbox):
    """Test that safe code passes validation."""
    code = "import math\nprint(math.sqrt(4))"
    # Should not raise
    sandbox.validate_code(code)

def test_validate_code_blocked(sandbox):
    """Test that blocked modules raise SecurityViolationError."""
    unsafe_code = "import os\nos.system('echo hack')"
    with pytest.raises(SecurityViolationError) as excinfo:
        sandbox.validate_code(unsafe_code)
    assert "Import of 'os' is blocked" in str(excinfo.value)

def test_validate_code_blocked_from(sandbox):
    """Test that blocked modules via from-import raise SecurityViolationError."""
    unsafe_code = "from shutil import rmtree"
    with pytest.raises(SecurityViolationError):
        sandbox.validate_code(unsafe_code)

def test_validate_code_allowed_override(mock_config):
    """Test that allowed imports override the block list."""
    mock_config.sandbox.allowed_imports = ["os"]
    sandbox = SandboxManager(mock_config)
    
    code = "import os\nprint(os.getcwd())"
    # Should not raise
    sandbox.validate_code(code)

@pytest.mark.asyncio
async def test_get_uv_path_found_in_path(sandbox):
    """Test finding uv in PATH."""
    with patch("shutil.which", return_value="/usr/bin/uv"):
        assert sandbox._get_uv_path() == "/usr/bin/uv"

@pytest.mark.asyncio
async def test_get_uv_path_found_in_module(sandbox):
    """Test finding uv via python module if not in PATH."""
    with patch("shutil.which", return_value=None), \
         patch.dict(sys.modules, {"uv": MagicMock(find_uv_bin=lambda: "/pip/uv")}):
        assert sandbox._get_uv_path() == "/pip/uv"

@pytest.mark.asyncio
async def test_ensure_environment_creates_venv(sandbox):
    """Test that ensure_environment calls uv venv if dir missing."""
    with patch.object(Path, "exists",side_effect=[True, False]), \
         patch.object(SandboxManager, "_get_uv_path", return_value="uv"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        
        # Mock process
        mock_process = AsyncMock()
        # configure communicate to be awaitable and return tuple
        mock_process.communicate.return_value = (b"", b"") 
        mock_process.returncode = 0
        
        # create_subprocess_exec is async, returns the process
        mock_exec.return_value = mock_process
        
        await sandbox.ensure_environment()
        
        # Verify uv venv called
        assert mock_exec.call_count >= 1
        args = mock_exec.call_args_list[0][0]
        assert args[0] == "uv"
        assert args[1] == "venv"

@pytest.mark.asyncio
async def test_run_python_executes_code(sandbox):
    """Test run_python executes code and returns output."""
    code = "print('hello')"
    
    with patch("pathlib.Path.write_text") as mock_write, \
         patch("pathlib.Path.unlink") as mock_unlink, \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
             
        # Mock process
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"hello\n", b"")
        mock_process.returncode = 0
        mock_exec.return_value = mock_process
        
        result = await sandbox.run_python(code)
        
        assert result == "hello"
        mock_write.assert_called_once()
        # Verify python exe was used
        assert str(sandbox.python_exe) == mock_exec.call_args[0][0]

@pytest.mark.asyncio
async def test_run_python_timeout(sandbox):
    """Test strict timeout handling."""
    code = "import time; time.sleep(10)"
    
    with patch("pathlib.Path.write_text"), \
         patch("pathlib.Path.unlink"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
             
        mock_process = MagicMock() # Process object itself isn't async, its methods are mixed
        # But we mostly mock the async methods or kill
        
        # We need mock_process to be returned by await create_subprocess_exec
        # So create_subprocess_exec (AsyncMock) returns this object.
        
        # communicate is async
        mock_process.communicate = AsyncMock()
        
        # wait is async
        mock_process.wait = AsyncMock()
        
        # kill is sync
        mock_process.kill = MagicMock()
        
        mock_exec.return_value = mock_process
        
        result = await sandbox.run_python(code, timeout=1)
        
        assert "TimeoutError" in result
        mock_process.kill.assert_called_once()
