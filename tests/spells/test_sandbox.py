import ast
import asyncio
import builtins
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auric.core.config import AuricConfig
from auric.spells.sandbox import SandboxManager, SecurityViolationError


@pytest.fixture
def mock_config():
    config = MagicMock(spec=AuricConfig)
    config.sandbox = MagicMock()
    config.sandbox.allowed_imports = []
    return config


@pytest.fixture
def temp_home(tmp_path):
    with patch("auric.spells.sandbox.Path.home", return_value=tmp_path):
        yield tmp_path


@pytest.fixture
def manager(mock_config, temp_home):
    return SandboxManager(mock_config)


def test_init_paths(manager, temp_home):
    assert manager.sandbox_dir == temp_home / ".auric" / ".auric_sandbox"
    assert manager.temp_dir == temp_home / ".auric" / "temp"

    if sys.platform == "win32":
        assert manager.python_exe == manager.sandbox_dir / "Scripts" / "python.exe"
    else:
        assert manager.python_exe == manager.sandbox_dir / "bin" / "python"


def test_get_uv_path_in_path(manager):
    with patch("auric.spells.sandbox.shutil.which", return_value="/usr/bin/uv"):
        assert manager._get_uv_path() == "/usr/bin/uv"


def test_get_uv_path_in_package(manager):
    with patch("auric.spells.sandbox.shutil.which", return_value=None):
        with patch.dict("sys.modules", {"uv": MagicMock(find_uv_bin=MagicMock(return_value="/pkg/uv"))}):
            assert manager._get_uv_path() == "/pkg/uv"


def test_get_uv_path_not_found(manager):
    with patch("auric.spells.sandbox.shutil.which", return_value=None):
        with patch.dict("sys.modules", {"uv": None}):  # Mock ImportError equivalent or ensure it fails
            # We need to simulate ImportError for `import uv`
            real_import = builtins.__import__
            def mock_import(name, *args, **kwargs):
                if name == "uv":
                    raise ImportError()
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", mock_import):
                with pytest.raises(RuntimeError, match="The 'uv' tool is not found"):
                    manager._get_uv_path()


def test_validate_code_valid(manager):
    manager.validate_code("print('hello')\nmath.sqrt(4)")


def test_validate_code_syntax_error(manager):
    with pytest.raises(ValueError, match="Invalid Python code"):
        manager.validate_code("print('hello'")


def test_validate_code_blocked_import(manager):
    with pytest.raises(SecurityViolationError, match="policy"):
        manager.validate_code("import os")
    
    with pytest.raises(SecurityViolationError, match="policy"):
        manager.validate_code("from subprocess import run")


def test_validate_code_allowed_import(manager):
    manager.config.sandbox.allowed_imports = ["os"]
    manager.validate_code("import os")


@pytest.mark.asyncio
async def test_ensure_environment_new_success(manager, temp_home):
    with patch.object(manager, "_get_uv_path", return_value="/usr/bin/uv"):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        
        with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await manager.ensure_environment()
            
            assert manager.sandbox_dir.parent.exists()
            assert manager.temp_dir.exists()
            
            # Should have called uv venv and uv pip install
            assert mock_exec.call_count == 2
            mock_exec.assert_any_call("/usr/bin/uv", "venv", str(manager.sandbox_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


@pytest.mark.asyncio
async def test_ensure_environment_venv_fails(manager, temp_home):
    with patch.object(manager, "_get_uv_path", return_value="/usr/bin/uv"):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"error")
        mock_proc.returncode = 1
        
        with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="Could not create sandbox"):
                await manager.ensure_environment()


@pytest.mark.asyncio
async def test_ensure_environment_uv_not_found(manager, temp_home):
    with patch.object(manager, "_get_uv_path", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="The 'uv' tool is not found"):
            await manager.ensure_environment()


@pytest.mark.asyncio
async def test_ensure_environment_pip_fails(manager, temp_home):
    with patch.object(manager, "_get_uv_path", return_value="/usr/bin/uv"):
        mock_proc_venv = AsyncMock()
        mock_proc_venv.communicate.return_value = (b"", b"")
        mock_proc_venv.returncode = 0
        
        mock_proc_pip = AsyncMock()
        mock_proc_pip.communicate.return_value = (b"", b"pip error")
        mock_proc_pip.returncode = 1
        
        with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", side_effect=[mock_proc_venv, mock_proc_pip]):
            with pytest.raises(RuntimeError, match="Could not install sandbox packages"):
                await manager.ensure_environment()


@pytest.mark.asyncio
async def test_ensure_environment_existing_broken(manager, temp_home):
    manager.sandbox_dir.mkdir(parents=True)
    manager.python_exe.parent.mkdir(parents=True)
    manager.python_exe.touch()
    
    with patch.object(manager, "_get_uv_path", return_value="/usr/bin/uv"):
        # First call checking python --version returns error
        mock_proc_check = AsyncMock()
        mock_proc_check.communicate.return_value = (b"", b"")
        mock_proc_check.returncode = 1
        
        mock_proc_venv = AsyncMock()
        mock_proc_venv.communicate.return_value = (b"", b"")
        mock_proc_venv.returncode = 0
        
        mock_proc_pip = AsyncMock()
        mock_proc_pip.communicate.return_value = (b"", b"")
        mock_proc_pip.returncode = 0
        
        with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", side_effect=[mock_proc_check, mock_proc_venv, mock_proc_pip]):
            await manager.ensure_environment()
            assert not manager.python_exe.exists() # Would be deleted during rm tree before recreate.


@pytest.mark.asyncio
async def test_ensure_environment_existing_check_exception(manager, temp_home):
    manager.sandbox_dir.mkdir(parents=True)
    manager.python_exe.parent.mkdir(parents=True)
    manager.python_exe.touch()
    
    with patch.object(manager, "_get_uv_path", return_value="/usr/bin/uv"):
        mock_proc_venv = AsyncMock()
        mock_proc_venv.communicate.return_value = (b"", b"")
        mock_proc_venv.returncode = 0
        
        mock_proc_pip = AsyncMock()
        mock_proc_pip.communicate.return_value = (b"", b"")
        mock_proc_pip.returncode = 0
        
        with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", side_effect=[Exception("Check failed"), mock_proc_venv, mock_proc_pip]):
            await manager.ensure_environment()


@pytest.mark.asyncio
async def test_run_python_success(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"output\n", b"")
    mock_proc.returncode = 0
    
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await manager.run_python("print('hello')")
        assert result == "output"


@pytest.mark.asyncio
async def test_run_python_failure(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"error")
    mock_proc.returncode = 1
    
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await manager.run_python("1/0")
        assert "Execution failed (Exit Code 1):" in result
        assert "error" in result


@pytest.mark.asyncio
async def test_run_python_timeout(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    
    mock_proc = AsyncMock()
    # Simulate timeout by making communicate raise TimeoutError inside wait_for
    # asyncio.wait_for will raise TimeoutError
    
    async def mock_wait_for(*args, **kwargs):
        raise asyncio.TimeoutError()
        
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("auric.spells.sandbox.asyncio.wait_for", side_effect=mock_wait_for):
            result = await manager.run_python("while True: pass", timeout=1)
            assert "TimeoutError: Code execution timed out after 1 seconds." in result
            mock_proc.kill.assert_called_once()
            mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_python_val_error(manager, temp_home):
    # syntax error or blocked import
    with pytest.raises(SecurityViolationError, match="policy"):
        await manager.run_python("import os")


@pytest.mark.asyncio
async def test_run_python_no_output(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await manager.run_python("x = 1")
        assert result == "Success (No Output)"


@pytest.mark.asyncio
async def test_run_python_cleanup_fails(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"hi", b"")
    mock_proc.returncode = 0
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("pathlib.Path.unlink", side_effect=Exception("unlink failed")):
            with patch("auric.spells.sandbox.logger.warning") as mock_logger:
                result = await manager.run_python("print('hi')")
                assert result == "hi"
                mock_logger.assert_called_once()


def test_init_paths_non_win32(manager, temp_home):
    with patch("auric.spells.sandbox.sys.platform", "linux"):
        m = SandboxManager(manager.config)
        assert m.python_exe == m.sandbox_dir / "bin" / "python"


@pytest.mark.asyncio
async def test_run_python_timeout_kill_fails(manager, temp_home):
    manager.temp_dir.mkdir(parents=True)
    mock_proc = AsyncMock()
    mock_proc.kill = MagicMock(side_effect=Exception("kill failed"))
    
    async def mock_wait_for(*args, **kwargs):
        raise asyncio.TimeoutError()
        
    with patch("auric.spells.sandbox.asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("auric.spells.sandbox.asyncio.wait_for", side_effect=mock_wait_for):
            result = await manager.run_python("while True: pass", timeout=1)
            assert "TimeoutError: Code execution timed out after 1 seconds." in result


@pytest.mark.asyncio
async def test_run_python_system_error(manager, temp_home):
    with patch("pathlib.Path.write_text", side_effect=Exception("Write failed")):
        result = await manager.run_python("print('hello')")
        assert "System Error: Write failed" in result
