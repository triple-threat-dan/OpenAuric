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
    logs: List[str] # Last 50 lines
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

    # 2. Get Logs
    # In a real system, we'd tap into a shared ring buffer.
    # For now, we'll return a placeholder or implement a simple buffer in the app state later.
    logs = getattr(request.app.state, "log_buffer", ["System initialized."])

    # 3. Get Stats
    # Placeholder
    stats = {
        "status": "ONLINE",
        "active_model": "Local",
        "memory_usage": "N/A"
    }

    return StatusResponse(
        focus_state=focus_data,
        logs=logs,
        stats=stats
    )

@router.post("/api/chat")
async def chat(request: Request, chat_req: ChatRequest):
    """
    pushes a user message to the event bus.
    """
    event_bus: asyncio.Queue = getattr(request.app.state, "event_bus", None)
    if not event_bus:
        raise HTTPException(status_code=503, detail="Event bus not available.")
    
    msg = {
        "level": "USER",
        "message": chat_req.message,
        "source": "WEB"
    }
    await event_bus.put(msg)
    return {"status": "Message sent"}

# We'll mount the static files in the main daemon setup.
