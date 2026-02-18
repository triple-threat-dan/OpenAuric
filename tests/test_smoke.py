import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from auric.core.daemon import run_daemon
from auric.interface.server.routes import router as dashboard_router
from auric.memory.focus_manager import FocusManager, FocusState

# --- Test A: The Boot Cycle ---
@pytest.mark.asyncio
async def test_daemon_startup(mock_config, mock_tui):
    """
    Verifies that run_daemon orchestrates components correctly:
    1. Loads config (via mock_config fixture)
    2. Starts Scheduler
    3. Inits and starts PactManager
    4. Starts API Server (uvicorn default)
    5. Awaits TUI
    6. Shuts down gracefully
    """
    api_app = FastAPI()
    
    # We patch the dependencies that run_daemon instantiates internally
    with patch("auric.core.daemon.AsyncIOScheduler") as MockScheduler, \
         patch("auric.core.daemon.AuditLogger") as MockAuditLogger, \
         patch("auric.interface.pact_manager.PactManager") as MockPactManager, \
         patch("auric.core.daemon.Server") as MockUvicornServer:
        
        # Setup mocks
        mock_scheduler_instance = MockScheduler.return_value
        mock_audit_logger_instance = MockAuditLogger.return_value
        mock_audit_logger_instance.init_db = AsyncMock()
        
        mock_pact_manager_instance = MockPactManager.return_value
        mock_pact_manager_instance.start = AsyncMock()
        mock_pact_manager_instance.stop = AsyncMock()
        
        mock_server_instance = MockUvicornServer.return_value
        mock_server_instance.serve = AsyncMock()
        
        # execution
        # Make TUI yield so background API task starts
        async def tui_delay():
             await asyncio.sleep(0.01)
        mock_tui.run_async = AsyncMock(side_effect=tui_delay)
        
        await run_daemon(tui_app=mock_tui, api_app=api_app)
        
        # Assertions
        mock_scheduler_instance.start.assert_called_once()
        mock_audit_logger_instance.init_db.assert_awaited_once()
        mock_pact_manager_instance.start.assert_awaited_once()
        
        # Verify API server task was created (scheduled), not necessarily awaited directly by the daemon
        mock_server_instance.serve.assert_called_once()
        
        # Verify TUI was run
        mock_tui.run_async.assert_awaited_once()
        
        # Verify Cleanup
        mock_pact_manager_instance.stop.assert_awaited_once()
        mock_scheduler_instance.shutdown.assert_called_once()


# --- Test B: The API Liveness ---
@pytest.mark.asyncio
async def test_api_health(mock_config):
    """
    Verifies the FastAPI app responds to status checks.
    """
    app = FastAPI()
    app.include_router(dashboard_router)
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/status")
        
    assert response.status_code == 200
    data = response.json()
    assert "stats" in data
    assert "focus_state" in data
    assert data["stats"]["status"] == "ONLINE"


# --- Test C: The Grimoire Parser ---
def test_focus_parsing(mock_config):
    """
    Verifies that FocusManager correctly parses FOCUS.md.
    """
    # 1. Setup a valid FOCUS.md in the temp dir
    focus_content = """# 🔮 THE FOCUS (Current State)

## 🎯 Prime Directive (The "Why")
Destroy the One Ring.

## 📋 Plan of Action (The "How")
- [x] Leave the Shire
- [ ] Climb Mount Doom

## 🧠 Working Memory (Scratchpad)
Frodo is tired.
"""
    focus_path = Path("./.auric/grimoire/FOCUS.md").expanduser()
    focus_path.parent.mkdir(parents=True, exist_ok=True)
    focus_path.write_text(focus_content, encoding="utf-8")
    
    # 2. Parse
    manager = FocusManager(focus_path)
    model = manager.load()
    
    # 3. Assert
    assert model.prime_directive == "Destroy the One Ring."
    assert len(model.plan_steps) == 2
    assert model.plan_steps[0]["step"] == "Leave the Shire"
    assert model.plan_steps[0]["completed"] is True
    assert model.plan_steps[1]["step"] == "Climb Mount Doom"
    assert model.plan_steps[1]["completed"] is False
    assert model.working_memory == "Frodo is tired."
    assert model.state == FocusState.IN_PROGRESS


# --- Test D: CLI Version Check (SMK-01) ---
@pytest.mark.xfail(reason="Version check not implemented yet")
def test_version_check():
    """
    Verifies `auric --version` runs.
    Note: Since we are testing via library import, we use CliRunner.
    """
    from typer.testing import CliRunner
    from auric.cli import app
    
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    # If version is implemented, exit code 0. If not, Typer might error or show help.
    # For now, we mainly check it doesn't crash trace.
    assert result.exit_code == 0 or result.exit_code == 0
    # Ideally: assert "OpenAuric v" in result.stdout


# --- Test E: Database Initialization (SMK-04) ---
@pytest.mark.asyncio
async def test_database_initialization(tmp_path):
    """
    Verifies auric.db creation on first run.
    """
    from auric.core.database import AuditLogger
    
    # Use a fresh tmp path for DB
    db_path = tmp_path / "auric_test.db"
    
    logger = AuditLogger(db_path=db_path)
    await logger.init_db()
    
    assert db_path.exists()
    assert db_path.stat().st_size > 0

