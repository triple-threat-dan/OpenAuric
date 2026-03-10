import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from auric.interface.server.routes import router
from auric.interface.server.auth import verify_token

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_token] = lambda: None
    app.state.shutdown_event = asyncio.Event()
    return app

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.mark.asyncio
async def test_shutdown_endpoint(client, app):
    """Test that POST /api/shutdown sets the shutdown_event."""
    assert not app.state.shutdown_event.is_set()
    
    response = client.post("/api/shutdown")
    
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert app.state.shutdown_event.is_set()

def test_shutdown_endpoint_no_event():
    """Test shutdown endpoint when event is missing from state."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_token] = lambda: None
    # No app.state.shutdown_event set
    
    client = TestClient(app)
    response = client.post("/api/shutdown")
    
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "not found" in response.json()["message"]
