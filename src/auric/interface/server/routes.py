"""
API Routes for the Arcane Library (Web Dashboard).
"""

import asyncio
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from auric.core.config import AuricConfig, ConfigLoader
from auric.interface.server.auth import verify_token

logger = logging.getLogger("auric.routes")
router = APIRouter(dependencies=[Depends(verify_token)])

# --- Models ---

class ChatRequest(BaseModel):
    message: str
    source: str = "WEB"
    session_id: Optional[str] = None

class StatusResponse(BaseModel):
    focus_state: Dict[str, Any]
    logs: List[str]
    chat_history: List[Dict[str, Any]]
    stats: Dict[str, str]
    current_session_id: Optional[str] = None

class LLMLogsResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int

class RenameRequest(BaseModel):
    name: str

class NewSessionRequest(BaseModel):
    context: str = "web"

# --- Routes ---

@router.get("/api/status", response_model=StatusResponse)
async def get_status(request: Request):
    """Returns the current engine status, focus state, and recent history."""
    state = request.app.state
    
    # 1. Focus State
    focus_manager = getattr(state, "focus_manager", None)
    if focus_manager:
        try:
            focus_model = focus_manager.load()
            focus_data = focus_model.model_dump()
            focus_data['state'] = focus_model.state.value
        except Exception as e:
            focus_data = {"error": f"Focus load error: {e}"}
    else:
        focus_data = {"error": "FocusManager not initialized"}

    # 2. Logs & Chat History
    logs = list(getattr(state, "web_log_buffer", ["System initialized."]))
    chat_history = []
    
    audit_logger = getattr(state, "audit_logger", None)
    current_sid = getattr(state, "current_session_id", None)
    
    if audit_logger:
        try:
            db_messages = await audit_logger.get_chat_history(limit=50, session_id=current_sid)
            chat_history = [
                {"level": msg.role, "message": msg.content, "source": "DB"} 
                for msg in db_messages
            ]
        except Exception as e:
            chat_history.append({"level": "ERROR", "message": f"History load error: {e}"})
    
    if not chat_history:
        chat_history = list(getattr(state, "web_chat_history", []))

    # 3. Stats
    config = getattr(state, "config", None)
    active_model = "Local (Default)"
    if config:
        if model_cfg := config.agents.models.get("smart_model"):
            active_model = f"{model_cfg.provider}/{model_cfg.model}"

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
        current_session_id=current_sid
    )

@router.get("/api/sessions")
async def get_sessions(request: Request):
    """Returns all chat sessions with active/inactive status."""
    state = request.app.state
    audit_logger = getattr(state, "audit_logger", None)
    if not audit_logger:
        return []
        
    try:
        sessions = await audit_logger.get_sessions()
        session_router = getattr(state, "session_router", None)
        current_web_sid = getattr(state, "current_session_id", None)
        
        active_sids = set(session_router.get_all_active_session_ids()) if session_router else set()
        if current_web_sid:
            active_sids.add(current_web_sid)
        
        for sess in sessions:
            sess["is_active"] = sess["session_id"] in active_sids
        
        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/chat/{session_id}")
async def get_session_chat(request: Request, session_id: str):
    """Returns full chat history for a specific session."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
         return []
    
    try:
        db_messages = await audit_logger.get_chat_history(limit=50, session_id=session_id)
        return [
            {
                "level": msg.role,
                "message": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "source": "DB"
            } for msg in db_messages
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/sessions/new")
async def new_session(request: Request, body: NewSessionRequest = None):
    """Starts a new chat session for the given context."""
    state = request.app.state
    context = body.context if body else "web"
    session_router = getattr(state, "session_router", None)
    audit_logger = getattr(state, "audit_logger", None)
    
    old_id = None
    new_id = None
    
    if context == "web":
        old_id = getattr(state, "current_session_id", None)
        if old_id and audit_logger:
            if gateway := getattr(state, "gateway", None):
                 await audit_logger.summarize_session(old_id, gateway)

        new_id = str(uuid4())
        state.current_session_id = new_id
        
        if audit_logger:
            await audit_logger.create_session(name="New Session", session_id=new_id)
        
        if (history := getattr(state, "web_chat_history", None)) is not None:
            history.clear()
            
        if focus_mgr := getattr(state, "focus_manager", None):
            focus_mgr.clear()
    else:
        if session_router:
            new_id = session_router.start_new_session(context)
            if audit_logger:
                await audit_logger.create_session(name=f"New Session ({context})", session_id=new_id)

    return {"status": "ok", "session_id": new_id, "previous_session_id": old_id, "context": context}

@router.post("/api/sessions/closeall")
async def close_all_sessions(request: Request):
    """Closes all active sessions and triggers summarization."""
    state = request.app.state
    session_router = getattr(state, "session_router", None)
    audit_logger = getattr(state, "audit_logger", None)
    gateway = getattr(state, "gateway", None)
    
    if not session_router:
        return {"status": "error", "message": "SessionRouter not available"}
    
    closed_pairs = session_router.close_all_sessions()
    web_sid = getattr(state, "current_session_id", None)
    
    summarized = 0
    if audit_logger and gateway:
        # Re-map for efficient checking
        closed_sids = {sid for _, sid in closed_pairs}
        all_to_summarize = list(closed_sids)
        if web_sid and web_sid not in closed_sids:
            all_to_summarize.append(web_sid)
            
        for sid in all_to_summarize:
            try:
                await audit_logger.summarize_session(sid, gateway)
                summarized += 1
            except Exception as e:
                logger.error(f"Failed to summarize session {sid}: {e}")
    
    # Reset web state
    state.current_session_id = str(uuid4())
    if (history := getattr(state, "web_chat_history", None)) is not None:
        history.clear()
    if focus_mgr := getattr(state, "focus_manager", None):
        focus_mgr.clear()
    
    return {"status": "ok", "summarized": summarized, "closed_count": len(closed_pairs)}

@router.post("/api/sessions/{session_id}/close")
async def close_session(request: Request, session_id: str):
    """Closes a specific active session."""
    state = request.app.state
    session_router = getattr(state, "session_router", None)
    audit_logger = getattr(state, "audit_logger", None)
    gateway = getattr(state, "gateway", None)
    
    async def _summarize(sid):
        if audit_logger and gateway and sid:
            try:
                await audit_logger.summarize_session(sid, gateway)
            except Exception as e:
                logger.error(f"Failed to summarize session {sid}: {e}")
    
    if session_router:
        active_map = session_router.list_active_contexts()
        found_context = next((ctx for ctx, sid in active_map.items() if sid == session_id), None)
        
        if found_context:
            await _summarize(session_id)
            session_router.close_session(found_context)
            return {"status": "ok", "context": found_context}
    
    if getattr(state, "current_session_id", None) == session_id:
        await _summarize(session_id)
        state.current_session_id = str(uuid4())
        if (history := getattr(state, "web_chat_history", None)) is not None:
            history.clear()
        if focus_mgr := getattr(state, "focus_manager", None):
            focus_mgr.clear()
        return {"status": "ok", "rotated": True}
    
    return {"status": "error", "message": "Session not found"}

@router.post("/api/sessions/{session_id}/rename")
async def rename_session(request: Request, session_id: str, body: RenameRequest):
    """Renames a session in the database."""
    if audit_logger := getattr(request.app.state, "audit_logger", None):
        await audit_logger.rename_session(session_id, body.name)
    return {"status": "ok"}

@router.post("/api/chat")
async def chat(request: Request, chat_req: ChatRequest):
    """Pushes a user message to the command bus."""
    command_bus = getattr(request.app.state, "command_bus", None)
    if not command_bus:
        raise HTTPException(status_code=503, detail="Command bus not available.")
    
    msg = {
        "level": "USER",
        "message": chat_req.message,
        "source": chat_req.source,
        "session_id": chat_req.session_id or getattr(request.app.state, "current_session_id", None)
    }
    await command_bus.put(msg)
    return {"status": "ok"}

@router.get("/api/system_logs")
async def get_system_logs(request: Request, limit: int = 100):
    """Returns recent lines from the system log file."""
    config = getattr(request.app.state, "config", None)
    if not config:
        return {"lines": []}
        
    try:
        log_dir = Path(config.agents.defaults.logging.log_dir)
        if not log_dir.is_absolute():
            log_dir = Path.cwd() / log_dir
            
        log_file = log_dir / "system.jsonl"
        if not log_file.exists():
            return {"lines": []}
            
        with open(log_file, "r", encoding="utf-8") as f:
            last_lines = deque(f, maxlen=limit)
            
        parsed_lines = []
        for line in last_lines:
            try:
                parsed_lines.append(json.loads(line))
            except json.JSONDecodeError:
                parsed_lines.append({"raw": line})
                
        parsed_lines.reverse()
        return {"lines": parsed_lines}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/llm_logs", response_model=LLMLogsResponse)
async def get_llm_logs(request: Request, limit: int = 20, offset: int = 0):
    """Returns paginated LLM audit logs."""
    audit_logger = getattr(request.app.state, "audit_logger", None)
    if not audit_logger:
        return {"items": [], "total": 0}
    
    try:
        result = await audit_logger.get_llm_logs(limit, offset)
        return {
            "total": result["total"],
            "items": [item.model_dump() for item in result["items"]]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/heartbeat")
async def trigger_heartbeat(request: Request):
    """Triggers an immediate system heartbeat pulse."""
    from auric.core.heartbeat import run_heartbeat_task
    
    command_bus = getattr(request.app.state, "command_bus", None)
    await run_heartbeat_task(command_bus=command_bus)
    return {"status": "ok"}

@router.post("/api/shutdown")
async def shutdown_daemon(request: Request):
    """Triggers a graceful shutdown of the daemon."""
    shutdown_event = getattr(request.app.state, "shutdown_event", None)
    if shutdown_event:
        shutdown_event.set()
        return {"status": "ok", "message": "Shutdown initiated."}
    return {"status": "error", "message": "Shutdown event not found."}

@router.get("/api/settings")
async def get_settings(request: Request):
    """Returns the current raw configuration settings."""
    config: AuricConfig = getattr(request.app.state, "config", None)
    if not config:
        raise HTTPException(status_code=500, detail="Configuration not loaded.")
    # Return dump using aliases to match the auric.json keys exactly
    return config.model_dump(by_alias=True)

@router.post("/api/settings")
async def update_settings(request: Request):
    """Updates configuration settings and saves to disk."""
    try:
        body = await request.json()
        
        # Ensure that sandbox allowed_imports is set to an empty list if not provided
        if "sandbox" in body and "allowed_imports" not in body["sandbox"]:
             body["sandbox"]["allowed_imports"] = []

        # Validate by instantiating the model
        new_config = AuricConfig(**body)
        
        # Update app state
        request.app.state.config = new_config
        
        # Save to disk
        ConfigLoader.save(new_config)
        
        return {"status": "ok", "message": "Settings updated successfully."}
    except Exception as e:
        logger.error(f"Failed to update settings: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {str(e)}")
