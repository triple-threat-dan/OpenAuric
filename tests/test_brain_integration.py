import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from auric.core.daemon import run_daemon

@pytest.mark.asyncio
async def test_brain_loop_processing(mock_config_obj):
    """
    Verifies that the brain_loop in run_daemon correctly:
    1. Consumes messages from event_bus.
    2. Calls RLMEngine.think().
    3. Puts response back on event_bus.
    """
    mock_tui = MagicMock()
    # Use real FastAPI to ensure state persistence works correctly
    from fastapi import FastAPI
    mock_api = FastAPI()
    
    # We need to control the event bus to inject messages and check responses
    # But run_daemon creates its own event_bus.
    # However, it assigns it to api_app.state.event_bus.
    # We can access it via mock_api.state.event_bus IF we use a real object there?
    # Or checking what run_daemon does: `api_app.state.event_bus = event_bus`
    
    # Let's Patch the dependencies
    with patch("auric.core.daemon.Server") as MockServer, \
         patch("auric.core.daemon.load_config", return_value=mock_config_obj), \
         patch("auric.core.daemon.AuditLogger") as MockAudit, \
         patch("auric.interface.pact_manager.PactManager") as MockPact, \
         patch("auric.core.daemon.AsyncIOScheduler"), \
         patch("auric.brain.rlm.RLMEngine") as MockRLM, \
         patch("auric.brain.llm_gateway.LLMGateway"), \
         patch("auric.memory.librarian.GrimoireLibrarian"), \
         patch("auric.memory.focus_manager.FocusManager"):
         
         # Setup Async Methods
         MockAudit.return_value.init_db = AsyncMock()
         MockPact.return_value.start = AsyncMock()
         MockPact.return_value.stop = AsyncMock()
         
         # Setup RLM Mock
         mock_engine = MockRLM.return_value
         mock_engine.think = AsyncMock(return_value="I have processed your thought.")
         
         # Setup Server Mock to avoid blocking
         MockServer.return_value.serve = AsyncMock()
         
         # Logic to inject message once TUI starts
         async def tui_injector():
             # Access the event bus created in run_daemon
             # We can find it via mock_api.state.event_bus assignment
             assert hasattr(mock_api.state, "event_bus")
             bus = mock_api.state.event_bus
             
             # Send User Message
             await bus.put({"level": "USER", "message": "Hello Brain"})
             
             # Give brain loop time to process
             await asyncio.sleep(0.1)
             
             # Check if response is on the bus
             try:
                 response = await asyncio.wait_for(bus.get(), timeout=2.0)
                 assert response["level"] == "AGENT"
                 assert response["message"] == "I have processed your thought."
                 assert response["source"] == "BRAIN"
             except asyncio.TimeoutError:
                 # Debug: Check if brain loop crashed?
                 pytest.fail("Brain did not respond in time (2.0s). Check logs for 'Brain Loop Critical Error'.")
                 
         mock_tui.run_async = AsyncMock(side_effect=tui_injector)
         
         # Run Daemon
         await run_daemon(tui_app=mock_tui, api_app=mock_api)
         
         # Verify Think was called
         mock_engine.think.assert_awaited_with("Hello Brain")
