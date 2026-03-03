import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auric.interface.server.routes import router
from auric.interface.server.auth import verify_token
from auric.memory.focus_manager import FocusManager
from uuid import uuid4
import json

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    # Override verify_token to always succeed for route logic tests
    app.dependency_overrides[verify_token] = lambda: None
    
    # Setup initial state
    app.state.web_log_buffer = ["Log line 1"]
    app.state.web_chat_history = []
    app.state.current_session_id = "test-session-id"
    app.state.config = MagicMock()
    app.state.audit_logger = AsyncMock()
    app.state.session_router = MagicMock()
    app.state.command_bus = AsyncMock()
    app.state.focus_manager = MagicMock()
    app.state.gateway = MagicMock()
    
    return app

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.mark.asyncio
async def test_get_status(client, app):
    # Mock FocusManager.load to return a valid focus model
    mock_focus = MagicMock()
    mock_focus.state.value = "ACTIVE"
    mock_focus.model_dump.return_value = {"goal": "test goal"}
    
    # Mock database history
    mock_msg = MagicMock()
    mock_msg.role = "user"
    mock_msg.content = "hello"
    app.state.audit_logger.get_chat_history.return_value = [mock_msg]
    
    with patch("auric.interface.server.routes.FocusManager") as MockFocusManager:
        MockFocusManager.return_value.load.return_value = mock_focus
        
        response = client.get("/api/status")
        
    assert response.status_code == 200
    data = response.json()
    assert data["focus_state"]["goal"] == "test goal"
    assert data["focus_state"]["state"] == "ACTIVE"
    assert data["logs"] == ["Log line 1"]
    assert data["chat_history"][0]["message"] == "hello"
    assert data["stats"]["status"] == "ONLINE"
    assert data["current_session_id"] == "test-session-id"

@pytest.mark.asyncio
async def test_get_sessions(client, app):
    # audit_logger.get_sessions is awaited in routes.py
    # Returns a list of dicts that routes.py iterates over to add is_active
    async def mock_get_sessions():
        return [
            {"session_id": "test-session-id", "message_count": 5},
            {"session_id": "other-session", "message_count": 2}
        ]
    
    app.state.audit_logger.get_sessions = mock_get_sessions
    app.state.session_router.get_all_active_session_ids.return_value = ["test-session-id"]
    
    response = client.get("/api/sessions")
    if response.status_code == 500:
        print(f"500 Error detail: {response.json()}")
    
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["is_active"] is True
    assert data[1]["is_active"] is False

@pytest.mark.asyncio
async def test_get_session_chat(client, app):
    mock_msg = MagicMock()
    mock_msg.role = "assistant"
    mock_msg.content = "I am Auric"
    mock_msg.timestamp = None
    app.state.audit_logger.get_chat_history.return_value = [mock_msg]
    
    response = client.get("/api/chat/some-session")
    
    assert response.status_code == 200
    data = response.json()
    assert data[0]["message"] == "I am Auric"
    assert data[0]["level"] == "assistant"

@pytest.mark.asyncio
async def test_new_session_web(client, app):
    app.state.web_chat_history = ["history to clear"]
    
    response = client.post("/api/sessions/new", json={"context": "web"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["context"] == "web"
    assert app.state.current_session_id == data["session_id"]
    assert app.state.web_chat_history == []
    app.state.audit_logger.create_session.assert_called_once()

@pytest.mark.asyncio
async def test_new_session_global(client, app):
    app.state.session_router.start_new_session.return_value = "global-sid"
    
    response = client.post("/api/sessions/new", json={"context": "global"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "global-sid"
    app.state.session_router.start_new_session.assert_called_with("global")

@pytest.mark.asyncio
async def test_close_all_sessions(client, app):
    app.state.session_router.close_all_sessions.return_value = [("discord", "sid1")]
    app.state.current_session_id = "web-sid"
    
    response = client.post("/api/sessions/closeall")
    
    assert response.status_code == 200
    assert app.state.current_session_id != "web-sid"
    app.state.audit_logger.summarize_session.assert_called()

@pytest.mark.asyncio
async def test_close_specific_session(client, app):
    app.state.session_router.list_active_contexts.return_value = {"discord:1": "sid1"}
    
    response = client.post("/api/sessions/sid1/close")
    
    assert response.status_code == 200
    app.state.session_router.close_session.assert_called_with("discord:1")
    app.state.audit_logger.summarize_session.assert_called_with("sid1", app.state.gateway)

@pytest.mark.asyncio
async def test_rename_session(client, app):
    response = client.post("/api/sessions/sid1/rename", json={"name": "New Name"})
    
    assert response.status_code == 200
    app.state.audit_logger.rename_session.assert_called_with("sid1", "New Name")

@pytest.mark.asyncio
async def test_chat(client, app):
    chat_payload = {"message": "Hello", "source": "WEB", "session_id": "sid1"}
    
    response = client.post("/api/chat", json=chat_payload)
    
    assert response.status_code == 200
    app.state.command_bus.put.assert_called_once()
    call_args = app.state.command_bus.put.call_args[0][0]
    assert call_args["message"] == "Hello"
    assert call_args["session_id"] == "sid1"

@pytest.mark.asyncio
async def test_get_system_logs(client):
    # Mocking config and file read
    mock_config = MagicMock()
    mock_config.agents.defaults.logging.log_dir = "logs"
    
    mock_log_content = json.dumps({"timestamp": "2026-03-03", "message": "test log"}) + "\n"
    
    # Patch load_config in the core config module since load_config is imported in routes.py
    with patch("auric.core.config.load_config", return_value=mock_config), \
         patch("auric.interface.server.routes.Path.exists", return_value=True):
        
        with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: [mock_log_content]))):
            response = client.get("/api/system_logs?limit=10")
            
    assert response.status_code == 200
    data = response.json()
    assert len(data["lines"]) == 1
    assert data["lines"][0]["message"] == "test log"

@pytest.mark.asyncio
async def test_get_llm_logs(client, app):
    mock_item = MagicMock()
    mock_item.model_dump.return_value = {"id": 1, "prompt": "test"}
    app.state.audit_logger.get_llm_logs.return_value = {
        "total": 1,
        "items": [mock_item]
    }
    
    response = client.get("/api/llm_logs")
    
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["prompt"] == "test"

@pytest.mark.asyncio
async def test_trigger_heartbeat(client, app):
    # Patch in the original module
    with patch("auric.core.heartbeat.run_heartbeat_task", new_callable=AsyncMock) as mock_heartbeat:
        response = client.post("/api/heartbeat")
        
    assert response.status_code == 200
    mock_heartbeat.assert_called_once_with(command_bus=app.state.command_bus)
