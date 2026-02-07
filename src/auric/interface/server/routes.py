"""
API Routes for the Arcane Library (Web Dashboard).
"""
import asyncio
from typing import Dict, Any, List
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from auric.memory.focus_manager import FocusManager

router = APIRouter()

# --- Models ---

class ChatRequest(BaseModel):
    message: str

class StatusResponse(BaseModel):
    focus_state: Dict[str, Any]
    logs: List[str] # Last 100 lines
    chat_history: List[Dict[str, Any]] # Last 50 messages
    stats: Dict[str, str]

# --- Routes ---

@router.get("/api/status", response_model=StatusResponse)
async def get_status(request: Request):
    """
    Returns the current state of the agent:
    - Focus (from FOCUS.md)
    - Recent logs (from in-memory buffer, if available, or just empty for now)
    - System stats
    """
    # 1. Get Focus State
    # We assume the FocusManager is initialized somewhere or we act on the file directly.
    # Since we need to read the file, let's create a temporary manager or read directly.
    # For performance, maybe we should have a singleton injected, but for now:
    try:
        # Assuming standard path for now, or we could inject config
        focus_path = Path("~/.auric/grimoire/FOCUS.md").expanduser()
        focus_manager = FocusManager(focus_path)
        focus_model = focus_manager.load()
        focus_data = focus_model.model_dump()
        focus_data['state'] = focus_model.state.value # Convert enum to string
    except Exception as e:
        focus_data = {"error": str(e)}

    # 2. Get Logs & Chat History
    # We use the buffers injected by the daemon
    logs = list(getattr(request.app.state, "web_log_buffer", ["System initialized."]))
    chat_history = list(getattr(request.app.state, "web_chat_history", []))

    # 3. Get Stats
    config = getattr(request.app.state, "config", None)
    active_model = "Local (Default)"
    if config:
        active_model = config.agents.smart_model

    stats = {
        "status": "ONLINE",
        "active_model": active_model,
        "memory_usage": "N/A"
    }

    return StatusResponse(
        focus_state=focus_data,
        logs=logs,
        chat_history=chat_history,
        stats=stats
    )

@router.post("/api/chat")
async def chat(request: Request, chat_req: ChatRequest):
    """
    pushes a user message to the event bus.
    """
    command_bus: asyncio.Queue = getattr(request.app.state, "command_bus", None)
    if not command_bus:
        raise HTTPException(status_code=503, detail="Command bus not available.")
    
    msg = {
        "level": "USER",
        "message": chat_req.message,
        "source": "WEB"
    }
    await command_bus.put(msg)
    return {"status": "Message sent"}

# We'll mount the static files in the main daemon setup.
