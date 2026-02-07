import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from auric.core.daemon import run_daemon

@pytest.mark.asyncio
async def test_daemon_handles_uvicorn_system_exit(mock_config_obj):
    """
    Regression Test: Ensure run_daemon catches SystemExit from Uvicorn
    and doesn't crash the entire loop before TUI starts/stops.
    """
    # Mock TUI
    mock_tui = MagicMock()
    mock_tui.run_async = AsyncMock(return_value=None)
    
    # Mock API App
    mock_api = MagicMock()
    mock_api.state = MagicMock()
    
    # Mocks
    with patch("auric.core.daemon.Server") as MockServerClass, \
         patch("auric.core.daemon.load_config", return_value=mock_config_obj), \
         patch("auric.core.daemon.AuditLogger") as MockAuditClass, \
         patch("auric.interface.pact_manager.PactManager") as MockPactClass, \
         patch("auric.core.daemon.AsyncIOScheduler"):

        # Setup Server Mock
        mock_server_instance = MockServerClass.return_value
        async def mock_serve():
            raise SystemExit(1)
        mock_server_instance.serve = mock_serve
        
        # Setup Audit Logger Mock
        MockAuditClass.return_value.init_db = AsyncMock()
        
        # Setup Pact Manager Mock
        MockPactClass.return_value.start = AsyncMock()
        MockPactClass.return_value.stop = AsyncMock()
            
        # Run functionality
        try:
            await run_daemon(tui_app=mock_tui, api_app=mock_api)
        except SystemExit:
            pytest.fail("run_daemon raised SystemExit, it should have been caught!")
        
        # Verify signal handlers were overridden on the instance
        assert hasattr(mock_server_instance, 'install_signal_handlers')
        assert callable(mock_server_instance.install_signal_handlers)
        assert mock_server_instance.install_signal_handlers() is None

@pytest.mark.asyncio
async def test_daemon_disables_signals(mock_config_obj):
     pass
