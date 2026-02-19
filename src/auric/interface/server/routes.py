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

class NewSessionRequest(BaseModel):
    context: str = "web" # "web" or "global" or specific context like "discord:123"


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
        model_config = config.agents.models.get("smart_model")
        if model_config:
            active_model = f"{model_config.provider}/{model_config.model}"

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
async def new_session(request: Request, body: NewSessionRequest = None):
    """
    Starts a new chat session. 
    If context is 'web' (default), it just rotates the current UI session.
    If context is 'global', it might rotate everything (nuclear).
    """
    context = body.context if body else "web"
    
    # Access SessionRouter
    session_router = getattr(request.app.state, "session_router", None)
    audit_logger = getattr(request.app.state, "audit_logger", None)
    
    old_id = None
    new_id = None
    
    if context == "web":
        # 1. Archive Old Session (Web Specific)
        old_id = getattr(request.app.state, "current_session_id", None)
        
        if old_id and audit_logger:
            gateway = getattr(request.app.state, "gateway", None)
            if gateway:
                 await audit_logger.summarize_session(old_id, gateway)

        # 2. Create New Session
        new_id = str(uuid4())
        request.app.state.current_session_id = new_id
        
        # Create session in DB immediately
        if audit_logger:
            await audit_logger.create_session(name="New Session", session_id=new_id)
        
        # 3. Clear in-memory history
        chat_history = getattr(request.app.state, "web_chat_history", None)
        if chat_history is not None:
            chat_history.clear()
            
        # 4. Clear Focus
        focus_manager = getattr(request.app.state, "focus_manager", None)
        if focus_manager:
            focus_manager.clear()
            
    else:
        # Specific Context (e.g. "discord:12345")
        if session_router:
            new_id = session_router.start_new_session(context)
            # Create session in DB immediately
            if audit_logger:
                await audit_logger.create_session(name=f"New Session ({context})", session_id=new_id)

    return {"status": "New session started", "session_id": new_id, "previous_session_id": old_id, "context": context}

@router.post("/api/sessions/closeall")
async def close_all_sessions(request: Request):
    """
    Closes ALL active sessions (Nuclear Option).
    """
    session_router = getattr(request.app.state, "session_router", None)
    if session_router:
        session_router.close_all_sessions()
        return {"status": "All sessions closed"}
    return {"status": "error", "message": "SessionRouter not available"}

@router.post("/api/sessions/{session_id}/close")
async def close_session(request: Request, session_id: str):
    """
    Closes a specific session if it is active.
    This effectively just means removing it from the active map so a new one is generated next time.
    """
    session_router = getattr(request.app.state, "session_router", None)
    if session_router:
        # Find context for this session_id
        active_map = session_router.list_active_contexts()
        found_context = None
        for ctx, sid in active_map.items():
            if sid == session_id:
                found_context = ctx
                break
        
        if found_context:
            session_router.close_session(found_context)
            return {"status": "Session closed", "context": found_context}
        else:
             # Might be the web session?
             current_web_sid = getattr(request.app.state, "current_session_id", None)
             if current_web_sid == session_id:
                 # "Closing" web session just means generating a new one
                 new_id = str(uuid4())
                 request.app.state.current_session_id = new_id
                 return {"status": "Web session closed/rotated"}
                 
    return {"status": "Session not found or not active"}

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

@router.get("/api/system_logs")
async def get_system_logs(request: Request, limit: int = 100):
    """
    Returns the last N lines from the system log file.
    """
    try:
        from auric.core.config import load_config
        config = load_config()
        
        log_dir_str = config.agents.defaults.logging.log_dir
        log_dir = Path(log_dir_str)
        if not log_dir.is_absolute():
            log_dir = Path.cwd() / log_dir
            
        log_file = log_dir / "system.jsonl"
        
        if not log_file.exists():
            return {"lines": []}
            
        # Read last N lines (efficiently-ish)
        # For simplicity, we read all and take last N, but for production use `deque` or file seeking.
        # Given max size is 10MB, reading it all is okay-ish but not great.
        # Let's use `deque` from collections
        from collections import deque
        with open(log_file, "r", encoding="utf-8") as f:
            last_lines = deque(f, maxlen=limit)
            
        # Parse JSON
        parsed_lines = []
        import json
        for line in last_lines:
            try:
                parsed_lines.append(json.loads(line))
            except:
                parsed_lines.append({"raw": line})
                
        # Reverse to show newest first
        parsed_lines.reverse()
        return {"lines": parsed_lines}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@router.post("/api/heartbeat")
async def trigger_heartbeat(request: Request):
    """
    Triggers a real system heartbeat (Vigil).
    This checks active hours, reads HEARTBEAT.md, and wakes the agent if needed.
    """
    from auric.core.heartbeat import run_heartbeat_task
    
    command_bus = getattr(request.app.state, "command_bus", None)
    await run_heartbeat_task(command_bus=command_bus)
    
    return {"status": "Heartbeat triggered"}

# We'll mount the static files in the main daemon setup.
