import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from auric.core import daemon
from auric.core.config import AuricConfig

@pytest.fixture
def mock_config():
    config = AuricConfig()
    config.gateway.web_ui_token = "mock_shutdown_token"
    config.agents.models = {
        "heartbeat_model": MagicMock(model="hb-model"),
        "fast_model": {"model": "fast-model"}
    }
    return config

@pytest.mark.asyncio
async def test_shutdown_daemon_summarizes_sessions(mock_config):
    """Test that shutdown_daemon summarizes all active sessions."""
    mock_audit_logger = AsyncMock()
    mock_session_router = MagicMock()
    mock_gateway = MagicMock()
    mock_pact_manager = AsyncMock()
    mock_scheduler = MagicMock()
    
    # Setup active sessions
    mock_session_router.list_active_contexts.return_value = {
        "discord:1": "sess_1",
        "telegram:2": "sess_2"
    }
    
    tasks = [asyncio.create_task(asyncio.sleep(0.1)) for _ in range(2)]
    
    await daemon.shutdown_daemon(
        audit_logger=mock_audit_logger,
        session_router=mock_session_router,
        gateway=mock_gateway,
        config=mock_config,
        pact_manager=mock_pact_manager,
        scheduler=mock_scheduler,
        tasks_to_cancel=tasks
    )
    
    # Verify summarization was called for both sessions
    assert mock_audit_logger.summarize_session.await_count == 2
    mock_audit_logger.summarize_session.assert_any_await("sess_1", mock_gateway, model="hb-model")
    mock_audit_logger.summarize_session.assert_any_await("sess_2", mock_gateway, model="hb-model")
    
    # Verify sessions were closed in router
    assert mock_session_router.close_session.call_count == 2
    mock_session_router.close_session.assert_any_call("discord:1")
    mock_session_router.close_session.assert_any_call("telegram:2")
    
    # Verify subsystems stopped
    mock_pact_manager.stop.assert_awaited_once()
    mock_scheduler.shutdown.assert_called_once()
    mock_audit_logger.close.assert_awaited_once()
    
    # Verify tasks cancelled
    for t in tasks:
        assert t.cancelled()

@pytest.mark.asyncio
async def test_run_daemon_calls_shutdown_on_finish(mock_config):
    """Test that run_daemon triggers the shutdown helper in its finally block."""
    from fastapi import FastAPI
    app = FastAPI()
    app.state = MagicMock()
    
    with patch("auric.core.daemon.load_config", return_value=mock_config), \
         patch("auric.core.daemon.AuditLogger") as MockAuditLogger, \
         patch("auric.core.daemon.SessionRouter") as MockSessionRouter, \
         patch("auric.interface.pact_manager.PactManager") as MockPactManager, \
         patch("auric.core.daemon.AsyncIOScheduler") as MockScheduler, \
         patch("auric.core.daemon.shutdown_daemon", new_callable=AsyncMock) as mock_shutdown, \
         patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError), \
         patch("auric.core.daemon.ensure_workspace"), \
         patch("auric.core.daemon.Path.exists", return_value=True):
        
        # Ensure minimal setup to reach the try/finally
        audit_logger = MockAuditLogger.return_value
        audit_logger.init_db = AsyncMock()
        audit_logger.get_last_active_session_id = AsyncMock(return_value="last_sid")
        
        MockPactManager.return_value.start = AsyncMock()
        
        # RLMEngine also calls load_config via SystemLogger.get_instance()
        with patch("auric.core.config.load_config", return_value=mock_config):
            await daemon.run_daemon(None, app)
        
        # Verify shutdown helper was called
        mock_shutdown.assert_awaited_once()
