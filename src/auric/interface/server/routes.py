"""
API Routes for the Arcane Library (Web Dashboard).
"""
import asyncio
from typing import Dict, Any, List
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from auric.core.config import AURIC_WORKSPACE_DIR, AURIC_ROOT
from pydantic import BaseModel
from auric.interface.server.auth import verify_token

from auric.memory.focus_manager import FocusManager

router = APIRouter(dependencies=[Depends(verify_token)])

# --- Models ---

class ChatRequest(BaseModel):
    message: str

class StatusResponse(BaseModel):
    focus_state: Dict[str, Any]
    logs: List[str] # Last 100 lines
    chat_history: List[Dict[str, Any]] # Last 50 messages
    stats: Dict[str, str]
    current_session_id: str = None

class SessionSummary(BaseModel):
    session_id: str
    last_active: str = None
    message_count: int

class LLMLogsResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int

class RenameRequest(BaseModel):
    name: str

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
        focus_path = AURIC_ROOT / "memories" / "FOCUS.md"
        focus_manager = FocusManager(focus_path)
        focus_model = focus_manager.load()
        focus_data = focus_model.model_dump()
        focus_data['state'] = focus_model.state.value # Convert enum to string
    except Exception as e:
        focus_data = {"error": str(e)}

    # 2. Get Logs & Chat History
    # We use the buffers injected by the daemon
    logs = list(getattr(request.app.state, "web_log_buffer", ["System initialized."]))
    
    # Try to get persistent history from DB
    audit_logger = getattr(request.app.state, "audit_logger", None)
    chat_history = []
    
    current_sid = getattr(request.app.state, "current_session_id", None)
    
    if audit_logger:
        try:
            # FIX: Filter by current session ID to prevent bleeding
            db_messages = await audit_logger.get_chat_history(limit=50, session_id=current_sid)
            # Convert DB model to dict format expected by frontend
            for msg in db_messages:
                chat_history.append({
                    "level": msg.role,
                    "message": msg.content,
                    "source": "DB" # Or infer from role
                })
        except Exception as e:
            chat_history.append({"level": "ERROR", "message": f"Failed to load history: {e}"})
    
    if not chat_history:
        # Fallback to memory if DB empty or unavailable
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
        stats=stats,
        current_session_id=getattr(request.app.state, "current_session_id", None)
    )

@router.get("/api/sessions", response_model=List[SessionSummary])
async def get_sessions(request: Request):
    """Returns a list of all chat sessions."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        return []
    try:
        sessions = await audit_logger.get_sessions()
        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/chat/{session_id}")
async def get_session_chat(request: Request, session_id: str):
    """Returns the chat history for a specific session."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
         return []
    
    try:
        db_messages = await audit_logger.get_chat_history(limit=50, session_id=session_id)
        chat_history = []
        for msg in db_messages:
            chat_history.append({
                "level": msg.role,
                "message": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "source": "DB"
            })
        return chat_history
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/sessions/new")
async def new_session(request: Request):
    """Starts a new chat session."""
    
    # 1. Archive Old Session
    old_id = getattr(request.app.state, "current_session_id", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)
    
    if old_id and audit_logger:
        # Retrieve gateway from state (injected in daemon.py)
        gateway = getattr(request.app.state, "gateway", None)
        if gateway:
             # Run in background to not block UI? 
             # For now await it to ensure it finishes before context switch is fully "done" mentally
             # But technically we can fire and forget if we want speed.
             # User said "whenever a new session is created... trigger it".
             await audit_logger.summarize_session(old_id, gateway)
        else:
             print("Warning: Gateway not found in app state, skipping summary.")

    # 2. Create New Session
    new_id = str(uuid4())
    request.app.state.current_session_id = new_id
    
    # Create session in DB immediately so it shows up in lists
    if audit_logger:
        await audit_logger.create_session(name="New Session") # Optional: We could just let it be created on first msg
    
    # 3. Clear in-memory history so the UI starts fresh
    # (The old history is safely in DB)
    chat_history = getattr(request.app.state, "web_chat_history", None)
    if chat_history is not None:
        chat_history.clear()
        
    # 4. Clear Focus (New Session = Fresh Context)
    focus_manager = getattr(request.app.state, "focus_manager", None)
    if focus_manager:
        focus_manager.clear()

    return {"status": "New session started", "session_id": new_id, "previous_session_id": old_id}

@router.post("/api/sessions/{session_id}/rename")
async def rename_session(request: Request, session_id: str, body: RenameRequest):
    """Renames a session."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if audit_logger:
        await audit_logger.rename_session(session_id, body.name)
    return {"status": "ok"}

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
        "source": "WEB",
        "session_id": getattr(request.app.state, "current_session_id", None)
    }
    await command_bus.put(msg)
    return {"status": "Message sent"}

@router.get("/api/llm_logs", response_model=LLMLogsResponse)
async def get_llm_logs(request: Request, limit: int = 20, offset: int = 0):
    """Returns paginated LLM logs."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        return {"items": [], "total": 0}
    
    try:
        result = await audit_logger.get_llm_logs(limit, offset)
        # items are SQLModel objects, convert to dicts for safety/compatibility
        return {
            "total": result["total"],
            "items": [item.model_dump() for item in result["items"]]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# We'll mount the static files in the main daemon setup.
