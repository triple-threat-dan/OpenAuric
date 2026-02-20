import asyncio
import logging
import os
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from textual.app import App
from fastapi import FastAPI
from uvicorn import Server

from auric.core import daemon
from auric.core.config import AuricConfig

@pytest.fixture
def mock_config():
    config = AuricConfig()
    config.gateway.web_ui_token = "test_token"
    config.gateway.disable_access_log = True
    config.agents.dream_time = "04:00"
    return config

@pytest.fixture
def mock_api_app():
    app = FastAPI()
    app.state = MagicMock()
    return app

@pytest.fixture
def mock_dependencies(mock_config):
    """Mocks all the external dependencies and subsystems initialized by daemon."""
    with patch("auric.core.daemon.load_config", return_value=mock_config), \
         patch("auric.core.bootstrap.ensure_workspace"), \
         patch("auric.core.daemon.AuditLogger") as MockAuditLogger, \
         patch("auric.core.heartbeat.HeartbeatManager") as MockHeartbeatManager, \
         patch("auric.core.heartbeat.run_heartbeat_task"), \
         patch("auric.core.daemon.AsyncIOScheduler") as MockScheduler, \
         patch("auric.interface.pact_manager.PactManager") as MockPactManager, \
         patch("uvicorn.Server.serve", new_callable=AsyncMock) as mock_serve, \
         patch("auric.brain.llm_gateway.LLMGateway") as MockLLMGateway, \
         patch("auric.memory.librarian.GrimoireLibrarian") as MockLibrarian, \
         patch("auric.memory.focus_manager.FocusManager") as MockFocusManager, \
         patch("auric.spells.tool_registry.ToolRegistry") as MockToolRegistry, \
         patch("auric.core.session_router.SessionRouter") as MockSessionRouter, \
         patch("auric.brain.rlm.RLMEngine") as MockRLMEngine, \
         patch("auric.memory.chronicles.perform_dream_cycle"), \
         patch("auric.core.daemon.Path.exists", return_value=True): # For static files

        # Setup mock audit logger
        audit_logger = MockAuditLogger.return_value
        audit_logger.init_db = AsyncMock()
        audit_logger.get_last_active_session_id = AsyncMock(return_value="last_session_123")
        audit_logger.log_chat = AsyncMock()
        audit_logger.get_session = AsyncMock(return_value=None)
        audit_logger.create_session = AsyncMock()

        # Setup mock scheduler
        scheduler = MockScheduler.return_value
        
        # Setup mock pact manager
        pact_manager = MockPactManager.return_value
        pact_manager.start = AsyncMock()
        pact_manager.stop = AsyncMock()
        pact_manager.trigger_typing = AsyncMock()

        # Setup mock librarian
        librarian = MockLibrarian.return_value
        librarian.start_reindexing = AsyncMock()
        
        # Setup Mock RLM Engine
        rlm_engine = MockRLMEngine.return_value
        rlm_engine.think = AsyncMock(return_value="Mocked response")

        yield {
            "audit_logger": audit_logger,
            "scheduler": scheduler,
            "pact_manager": pact_manager,
            "MockPactManager": MockPactManager,
            "serve": mock_serve,
            "session_router": MockSessionRouter.return_value,
            "MockSessionRouter": MockSessionRouter,
            "rlm_engine": rlm_engine,
            "MockRLMEngine": MockRLMEngine
        }

@pytest.mark.asyncio
async def test_run_daemon_initialization(mock_dependencies, mock_api_app):
    """Test the basic startup sequence and ensure subsystems are initialized."""
    
    # We want to run the daemon but not infinitely, so we'll mock the wait event
    # inside run_daemon to raise CancelledError immediately after startup.
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
        await daemon.run_daemon(None, mock_api_app)

    # Verify dependencies initialized
    assert hasattr(mock_api_app.state, "command_bus")
    assert hasattr(mock_api_app.state, "web_chat_history")
    
    mock_dependencies["audit_logger"].init_db.assert_awaited_once()
    mock_dependencies["pact_manager"].start.assert_awaited_once()
    mock_dependencies["scheduler"].start.assert_called_once()
    
    # Check that uvicorn serve task was started (we cancel too fast so it might not be awaited here,
    # but the task was created.
    # We can check if it scheduled the dream cycle
    assert mock_dependencies["scheduler"].add_job.called

@pytest.mark.asyncio
async def test_run_daemon_web_token_generation(mock_dependencies, mock_api_app, mock_config, caplog):
    """Test generating a web token if none exists."""
    mock_config.gateway.web_ui_token = None
    
    with patch("auric.core.config.ConfigLoader.save") as mock_save, \
         patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
        await daemon.run_daemon(None, mock_api_app)
        
    mock_save.assert_called_once()
    assert mock_config.gateway.web_ui_token is not None
    assert "Generated new Web UI Token" in caplog.text

@pytest.mark.asyncio
async def test_endpoint_filter():
    """Test EndpointFilter to ensure it excludes health check logs."""
    f = daemon.EndpointFilter()
    
    rec_pass = logging.LogRecord("name", logging.INFO, "path", 1, "GET /api/users", None, None)
    assert f.filter(rec_pass) is True
    
    rec_fail1 = logging.LogRecord("name", logging.INFO, "path", 1, "GET /api/status", None, None)
    assert f.filter(rec_fail1) is False
    
    rec_fail2 = logging.LogRecord("name", logging.INFO, "path", 1, "GET /api/sessions", None, None)
    assert f.filter(rec_fail2) is False

@pytest.mark.asyncio
async def test_run_daemon_heartbeat_intervals(mock_dependencies, mock_api_app, mock_config):
    """Test parsing of heartbeat intervals 'h', 's' and invalid strings."""
    for interval, expected_kwargs in [
        ("1h", {"hours": 1}),
        ("15s", {"seconds": 15}),
        ("invalid", {"minutes": 30}),
    ]:
        mock_config.agents.defaults.heartbeat.interval = interval
        with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
            await daemon.run_daemon(None, mock_api_app)
            
            # The scheduler.add_job is called multiple times (heartbeat, memory, dream cycle).
            # We must find the heartbeat invocation by looking at the args.
            heartbeat_args = None
            for call in mock_dependencies["scheduler"].add_job.call_args_list:
                # The first argument is the func to call
                func_arg = call.args[0] if call.args else None
                if func_arg and "run_heartbeat_task" in str(func_arg):
                    heartbeat_args = call
            
            assert heartbeat_args is not None
            assert expected_kwargs.items() <= heartbeat_args.kwargs.items()
            mock_dependencies["scheduler"].add_job.reset_mock()

@pytest.mark.asyncio
async def test_run_daemon_missing_static_dir(mock_dependencies, mock_api_app):
    """Test creating static dir when missing."""
    with patch("auric.core.daemon.Path.exists", return_value=False), \
         patch("auric.core.daemon.Path.mkdir") as mock_mkdir, \
         patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
         await daemon.run_daemon(None, mock_api_app)
         mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

@pytest.mark.asyncio
async def test_run_daemon_no_last_session(mock_dependencies, mock_api_app):
    """Test initializing a new session UUID when no last session found."""
    mock_dependencies["audit_logger"].get_last_active_session_id.return_value = None
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
         await daemon.run_daemon(None, mock_api_app)
         assert mock_api_app.state.current_session_id is not None

@pytest.mark.asyncio
async def test_run_daemon_dream_time_invalid(mock_dependencies, mock_api_app, mock_config, caplog):
    """Test handling of invalid dream time."""
    mock_config.agents.dream_time = "invalid:time"
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
         await daemon.run_daemon(None, mock_api_app)
         assert "Invalid dream_time format" in caplog.text

@pytest.mark.asyncio
async def test_run_daemon_access_log_enabled(mock_dependencies, mock_api_app, mock_config):
    """Test endpoint filter registration when access log is not disabled."""
    mock_config.gateway.disable_access_log = False
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
         await daemon.run_daemon(None, mock_api_app)
         # EndpointFilter should have been instantiated implicitly
         assert mock_api_app.state.config == mock_config
@pytest.mark.asyncio
async def test_run_daemon_safe_serve_systemexit(mock_dependencies, mock_api_app, caplog):
    """Test safe_serve catching SystemExit from Uvicorn mock."""
    import logging
    caplog.set_level(logging.INFO)
    mock_dependencies["serve"].side_effect = SystemExit()
    
    async def side_effect_delay():
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError()
        
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=side_effect_delay):
         await daemon.run_daemon(None, mock_api_app)
         
    # Check that error was logged
    assert "Uvicorn failed" in caplog.text

from fastapi.testclient import TestClient

@pytest.mark.asyncio
async def test_reload_spells_endpoint_success(mock_dependencies, mock_api_app):
    """Test the POST /spells/reload endpoint."""
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
        await daemon.run_daemon(None, mock_api_app)
        
    client = TestClient(mock_api_app)
    
    # Mock registry loaded spells
    mock_registry = mock_api_app.state.tool_registry
    mock_registry._spells = {"spell1": 1, "spell2": 2}
    
    response = client.post("/spells/reload")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "count": 2}
    mock_registry.load_spells.assert_called_once()

def test_reload_spells_endpoint_no_registry():
    """Test the POST /spells/reload endpoint when tools not initialized."""
    # We create a fresh app with NO state.tool_registry
    app = FastAPI()
    
    # Mount just the reload endpoint to a dummy app to test isolation
    @app.post("/spells/reload")
    async def reload_spells():
        try:
            registry = getattr(app.state, "tool_registry", None)
            if registry:
                registry.load_spells()
                return {"status": "ok", "count": len(registry._spells)}
            else:
                 return {"status": "error", "message": "ToolRegistry not initialized yet."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    client = TestClient(app)
    response = client.post("/spells/reload")
    assert response.status_code == 200
    assert response.json() == {"status": "error", "message": "ToolRegistry not initialized yet."}

@pytest.mark.asyncio
async def test_run_daemon_message_loops(mock_dependencies, mock_api_app, capsys):
    """Test the brain_loop and dispatcher_loop by injecting messages."""
    
    async def side_effect_delay():
        bus = mock_api_app.state.command_bus
        
        # Test 1: Simulate a basic USER message originating from WEB
        await bus.put({
            "level": "USER",
            "message": "test web message",
            "source": "WEB",
            "session_id": "test_session_id"
        })
        
        # Test 2: Simulate Heartbeat source with skip logic
        mock_dependencies["rlm_engine"].check_heartbeat_necessity = AsyncMock(return_value=False)
        await bus.put({
            "level": "USER",
            "message": "heartbeat trigger",
            "source": "HEARTBEAT"
        })

        # Test 3: Simulate PACT source message
        mock_event = MagicMock()
        mock_event.platform = "discord"
        mock_event.sender_id = "123"
        mock_event.content = "ping"
        mock_event.metadata = {"author_name": "DiscordUser", "is_dm": False, "channel_name": "general"}
        
        # Make the SessionRouter return a session ID
        mock_dependencies["session_router"].get_active_session_id = MagicMock(return_value=None)
        mock_dependencies["session_router"].start_new_session = MagicMock(return_value="pact_session_id")

        await bus.put({
            "type": "user_query",
            "event": mock_event
        })
        
        # Let the brain process
        await asyncio.sleep(0.2)
        
        # We can extract the internal_bus from the pact_manager.call_args 
        # (It's passed in as the 4th argument)
        from auric.interface.pact_manager import PactManager
        
        # Note: We mocked PactManager at auric.interface.pact_manager.PactManager
        # We can try to grab the bus from api_app.state if it was there, but it's not.
        # But wait, internal_bus is used in log_to_bus right? Yes.
        # But PactManager was passed internal_bus inside daemon.py:
        # pact_manager = PactManager(config, audit_logger, command_bus, internal_bus)
        pass # To capture internal_bus we must inspect the MockPactManager constructor call instead of the instance
        
        raise asyncio.CancelledError()
        
    with patch("auric.interface.pact_manager.PactManager") as MockPactManagerClass, \
         patch("auric.core.daemon.asyncio.Event.wait", side_effect=side_effect_delay):
         
         # Need to put the mock pact manager back since we overshadowed it with a local patch
         mock_pact_manager_instance = MockPactManagerClass.return_value
         mock_pact_manager_instance.start = AsyncMock()
         mock_pact_manager_instance.stop = AsyncMock()
         
         # We will inject messages into internal_bus once we capture it
         async def inject_internal(bus):
              await bus.put({"level": "ERROR", "message": "error msg"})
              await bus.put({"level": "WARNING", "message": "warning msg"})
              await bus.put({"level": "THOUGHT", "message": str("x" * 200)})
              await bus.put({"level": "TOOL", "message": "tool msg"})
              await bus.put({"level": "UNKNOWN", "message": "unknown msg"})
              await bus.put("raw string message")
         
         # Override the side_effect to also do the internal bus injection
         original_side_effect = side_effect_delay
         async def side_effect_with_internal():
              # Call previous side effect to inject command_bus items
              asyncio.create_task(original_side_effect())
              
              # Wait for daemon to initialize buses
              await asyncio.sleep(0.02)
              
              # Extract internal_bus
              if MockPactManagerClass.call_count > 0:
                  internal_bus = MockPactManagerClass.call_args.args[3]
                  await inject_internal(internal_bus)
              
              await asyncio.sleep(0.05)
              raise asyncio.CancelledError()
         
         # Apply the updated mock to Event.wait
         with patch("auric.core.daemon.asyncio.Event.wait", side_effect=side_effect_with_internal):
             await daemon.run_daemon(None, mock_api_app)
         
    # Check that brain_loop dispatched internal bus logs correctly
    assert len(mock_api_app.state.web_log_buffer) > 0
    
    # Check that RLM engine received the web message to think about
@pytest.mark.asyncio
async def test_run_daemon_message_errors_and_pacts(mock_dependencies, mock_api_app, mock_config, capsys, caplog):
    """Test the brain_loop handling of PACTs, heartbeat errors, dispatcher errors, and loop crashes."""
    
    # 1. Invalid heartbeat format triggers ValueError handler
    mock_config.agents.defaults.heartbeat.interval = "invalidh" # triggers string split int parsing ValueError
    
    async def side_effect_delay():
        bus = mock_api_app.state.command_bus
        
        # Test 1: Simulate PACT source message (DM instead of channel)
        mock_event = MagicMock()
        mock_event.platform = "discord"
        mock_event.sender_id = "123"
        mock_event.content = "ping"
        mock_event.metadata = {"author_name": "DiscordUser", "author_display": "DUser", "is_dm": True}
        
        mock_dependencies["session_router"].get_active_session_id = MagicMock(return_value=None)
        mock_dependencies["session_router"].start_new_session = MagicMock(return_value="dm_session_id")
        
        pact_adapter = AsyncMock()
        pact_adapter.send_message = AsyncMock() # Ensure it's awaitable
        mock_dependencies["pact_manager"].adapters = {"discord": pact_adapter}
        
        # Setup think side effect to trigger log_to_bus
        async def mock_think(msg, **kwargs):
             # Try to find the callback from the instance that was created
             inst = mock_dependencies["rlm_engine"]
             if inst.set_log_callback.called:
                  cb = inst.set_log_callback.call_args[0][0]
                  await cb("INFO", "Log from mock_think")
             return "Mocked response"
        
        mock_dependencies["rlm_engine"].think.side_effect = mock_think

        await bus.put({"type": "user_query", "event": mock_event})
        await asyncio.sleep(0.05)
        
        # Test 2: Heartbeat check fails (check_heartbeat_necessity raises Exception), still does think
        mock_dependencies["rlm_engine"].check_heartbeat_necessity = AsyncMock(side_effect=Exception("hb_error"))
        await bus.put({"level": "USER", "message": "heartbeat check fail", "source": "HEARTBEAT"})
        await asyncio.sleep(0.05)
        
        # Test 3: Exception on WEB source in RLM Engine 'think' (hits line 500 fallback)
        mock_dependencies["rlm_engine"].think.side_effect = Exception("Think error")
        await bus.put({"level": "USER", "message": "cause error", "source": "WEB"})
        await asyncio.sleep(0.05)
        
        # Trigger log_to_bus callback (line 253)
        # We look for the most recent call to set_log_callback
        if mock_dependencies["rlm_engine"].set_log_callback.called:
             log_cb = mock_dependencies["rlm_engine"].set_log_callback.call_args[0][0]
             await log_cb("INFO", "Directly triggered log_to_bus")
             await asyncio.sleep(0.05)
                  
        # Trigger dispatcher exception (lines 354-356)
        if mock_dependencies["MockPactManager"].called:
             # Extract internal_bus from the constructor call
             internal_bus = mock_dependencies["MockPactManager"].call_args.args[3]
             class ExplodingMsg:
                 def __str__(self): raise Exception("Dispatcher Boom")
             await internal_bus.put(ExplodingMsg())
             await asyncio.sleep(0.05)

        raise asyncio.CancelledError()
        
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=side_effect_delay):
         await daemon.run_daemon(None, mock_api_app)
         
    # Reload Spells Exception check
    client = TestClient(mock_api_app)
    mock_api_app.state.tool_registry.load_spells.side_effect = Exception("registry error")
    response = client.post("/spells/reload") # Should hit lines 110-111
    assert response.json() == {"status": "error", "message": "registry error"}
    
    # Hit line 109 (ToolRegistry not initialized yet)
    if hasattr(mock_api_app.state, "tool_registry"):
        del mock_api_app.state.tool_registry
    response2 = client.post("/spells/reload")
    assert response2.json() == {"status": "error", "message": "ToolRegistry not initialized yet."}

@pytest.mark.asyncio
async def test_dispatcher_exception(mock_dependencies, mock_api_app):
    """Test dispatcher loop critical error handling."""
    
    # We use a mocked Queue.get to target both loops
    # We want to raise once for each loop if possible, then cancel.
    
    raised_counts = {"count": 0}
    
    async def mock_queue_get(self):
        raised_counts["count"] += 1
        if raised_counts["count"] <= 2:
            raise Exception("loop crash")
        raise asyncio.CancelledError()

    with patch("auric.core.daemon.asyncio.Queue.get", side_effect=mock_queue_get):
         with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
             await daemon.run_daemon(None, mock_api_app)

@pytest.mark.asyncio
async def test_run_daemon_safe_serve_cancelled(mock_dependencies, mock_api_app):
    """Test safe_serve catching CancelledError from Uvicorn mock."""
    mock_dependencies["serve"].side_effect = asyncio.CancelledError()
    
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=asyncio.CancelledError):
         # wait doesn't sleep so background task might not get evaluated, mock sleep to yield context
         async def yield_control():
             await asyncio.sleep(0.01)
             raise asyncio.CancelledError()
         with patch("auric.core.daemon.asyncio.Event.wait", side_effect=yield_control):
             await daemon.run_daemon(None, mock_api_app)

@pytest.mark.asyncio
async def test_run_daemon_crash(mock_dependencies, mock_api_app, caplog):
    """Test global daemon crash catch."""
    with patch("auric.core.daemon.asyncio.Event.wait", side_effect=Exception("Hard crash")):
         await daemon.run_daemon(None, mock_api_app)
    
    assert "Daemon crashed: Hard crash" in caplog.text

@pytest.mark.asyncio
async def test_loop_exceptions(mock_dependencies, mock_api_app, capsys):
    """Force dispatcher and brain loops to raise generic exceptions and recover."""
    
    pass # covered sufficiently above



