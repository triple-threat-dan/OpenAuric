"""
The AuricDaemon: Core orchestration engine for OpenAuric.

This module is responsible for initializing and managing the lifecycle of:
1. The Terminal User Interface (Textual)
2. The REST API (FastAPI/Uvicorn)
3. The Task Scheduler (APScheduler)

It ensures all three components run within the same asyncio event loop,
sharing memory and state (like the internal event bus).
"""

import asyncio
import logging
import os
from collections import deque
from typing import Optional, Deque, Dict, Any
from uuid import uuid4
from pathlib import Path

from textual.app import App
from uvicorn import Config, Server
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import load_config, AURIC_ROOT
from .database import AuditLogger
# from auric.interface.tui.app import AuricTUI # TUI Disabled
from auric.interface.server.routes import router as dashboard_router
from rich.console import Console
from rich.text import Text
from datetime import datetime

logger = logging.getLogger("auric.daemon")
console = Console()

class EndpointFilter(logging.Filter):
    """
    Filter out health checks and status polling from access logs.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().find("/api/status") == -1 and record.getMessage().find("/api/sessions") == -1

async def run_daemon(tui_app: Optional[App], api_app: FastAPI) -> None:
    """
    Entry point for the OpenAuric Daemon.
    
    Args:
        tui_app: The Textual App instance for the TUI (optional, but usually AuricTUI).
        api_app: The FastAPI instance for the REST API.
    """
    # 0. Load Configuration
    config = load_config()
    logger.info(f"OpenAuric Root: {AURIC_ROOT}")
    
    # Security: Ensure Web UI Token exists
    if not config.gateway.web_ui_token:
        import secrets
        from auric.core.config import ConfigLoader
        token = secrets.token_urlsafe(32)
        config.gateway.web_ui_token = token
        ConfigLoader.save(config)
        logger.warning(f"Generated new Web UI Token: {token}")
        console.print(f"[bold yellow]Generated new Web UI Token: {token}[/bold yellow]")
        console.print("Use 'auric token' to retrieve it later.")

    logger.info(f"Starting Auric Daemon (PID {os.getpid()})...")
    
    # 0. Bootstrap Workspace
    from auric.core.bootstrap import ensure_workspace
    ensure_workspace()

    # 1. Setup Internal Buses
    # `command_bus`: Inputs from Users (TUI, API, Pacts) -> Brain
    # `internal_bus`: Raw Output from Brain/System -> Dispatcher
    command_bus: asyncio.Queue = asyncio.Queue()
    internal_bus: asyncio.Queue = asyncio.Queue()
    
    # Consumers
    # tui_bus: asyncio.Queue = asyncio.Queue() # TUI Disabled
    web_chat_history: Deque[Dict[str, Any]] = deque(maxlen=50) # Recent chat
    web_log_buffer: Deque[str] = deque(maxlen=100) # Raw logs
    
    # Inject buses into API state
    api_app.state.command_bus = command_bus
    api_app.state.web_chat_history = web_chat_history
    api_app.state.web_log_buffer = web_log_buffer
    api_app.state.web_log_buffer = web_log_buffer
    api_app.state.config = config
    
    # Initialize active session later after DB load
    # api_app.state.current_session_id = str(uuid4())
    # We will inject audit_logger later after init

    # 1.1 Configure API App (Routes & Static)
    # Important: Include explicit routers BEFORE mounting catch-all StaticFiles at root "/"
    api_app.include_router(dashboard_router)

    @api_app.post("/spells/reload")
    async def reload_spells():
        try:
            # We access registry from state, which might be populated later
            # Logic robust to missing registry if called too early
            registry = getattr(api_app.state, "tool_registry", None)
            if registry:
                registry.load_spells()
                return {"status": "ok", "count": len(registry._spells)}
            else:
                 return {"status": "error", "message": "ToolRegistry not initialized yet."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Mount static files
    static_path = Path(__file__).parent.parent / "interface" / "server" / "static"
    if not static_path.exists():
        logger.warning(f"Static path {static_path} not found. Creating...")
        static_path.mkdir(parents=True, exist_ok=True)
        
    api_app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")

    # 2. Setup Scheduler (Heartbeat & Dream Cycle)
    # 2. Setup Scheduler (Heartbeat & Dream Cycle)
    scheduler = AsyncIOScheduler()
    
    # 3. Setup Database & Audit Logger
    audit_logger = AuditLogger()
    await audit_logger.init_db()
    api_app.state.audit_logger = audit_logger

    # Load Last Session
    last_session_id = await audit_logger.get_last_active_session_id()
    if last_session_id:
        api_app.state.current_session_id = last_session_id
        logger.info(f"Resuming session: {last_session_id}")
    else:
        new_sid = str(uuid4())
        api_app.state.current_session_id = new_sid
        logger.info(f"Starting new session: {new_sid}")

    # 2.1 Initialize HeartbeatManager with Logger (Singleton)
    from auric.core.heartbeat import HeartbeatManager, run_heartbeat_task
    # Singleton Init
    HeartbeatManager(audit_logger)

    # 2.2 Schedule Heartbeat
    if config.agents.defaults.heartbeat.enabled:
        interval_str = config.agents.defaults.heartbeat.interval
        kwargs = {}
        try:
            if interval_str.endswith("m"):
                 kwargs["minutes"] = int(interval_str[:-1])
            elif interval_str.endswith("h"):
                 kwargs["hours"] = int(interval_str[:-1])
            elif interval_str.endswith("s"):
                 kwargs["seconds"] = int(interval_str[:-1])
            else:
                 logger.warning(f"Invalid heartbeat interval '{interval_str}', defaulting to 30m")
                 kwargs["minutes"] = 30
        except ValueError:
             logger.warning(f"Could not parse heartbeat interval '{interval_str}', defaulting to 30m")
             kwargs["minutes"] = 30
             
        scheduler.add_job(run_heartbeat_task, 'interval', args=[command_bus], **kwargs)
        logger.info(f"Heartbeat scheduled every {interval_str}.")

    scheduler.start()
    logger.info("Scheduler started.")

    # 3.1 Setup Pact Manager (Omni-Channel)
    from auric.interface.pact_manager import PactManager
    pact_manager = PactManager(config, audit_logger, command_bus, internal_bus)
    await pact_manager.start()
    logger.info("PactManager started.")

    # 4. Setup FastAPI (Uvicorn)
    # We must run Uvicorn manually to keep it in our existing asyncio loop.


    uvi_config = Config(
        app=api_app, 
        host=config.gateway.host,
        port=config.gateway.port, 
        loop="asyncio",
        log_level="info" if logger.level <= logging.INFO else "warning",
    )
    server = Server(uvi_config)
    
    # Preventing Uvicorn from overriding signal handlers (Textual handles Ctrl+C)
    server.install_signal_handlers = lambda: None

    # Wrapper to catch Uvicorn's SystemExit on startup failure
    async def safe_serve():
        try:
            await server.serve()
        except SystemExit:
            logger.error("Uvicorn failed to start (likely port in use).")
        except asyncio.CancelledError:
            pass # Expected on shutdown
            
    # Launch server as a background task
    # Apply filter to uvicorn access log
    access_logger = logging.getLogger("uvicorn.access")
    if config.gateway.disable_access_log:
        access_logger.disabled = True
    else:
        access_logger.addFilter(EndpointFilter())
    
    api_task = asyncio.create_task(safe_serve())
    logger.info(f"API Server starting on {config.gateway.host}:{config.gateway.port}")

    # 5. Initialize Brain (RLM Engine) & Dependencies
    # Dependencies
    from auric.brain.llm_gateway import LLMGateway
    from auric.memory.librarian import GrimoireLibrarian
    from auric.memory.focus_manager import FocusManager
    from auric.brain.rlm import RLMEngine
    from auric.spells.tool_registry import ToolRegistry
    from auric.core.session_router import SessionRouter

    gateway = LLMGateway(config, audit_logger=audit_logger)
    
    librarian = GrimoireLibrarian()
    librarian.start()
    
    focus_path = AURIC_ROOT / "memories" / "FOCUS.md"
    focus_manager = FocusManager(focus_path) # Assumes file exists or handled by engine
    
    tool_registry = ToolRegistry(config, librarian=librarian)
    session_router = SessionRouter(AURIC_ROOT / "active_sessions.json")
    
    # Inject into API state and add reload endpoint
    api_app.state.tool_registry = tool_registry
    api_app.state.focus_manager = focus_manager
    api_app.state.gateway = gateway
    api_app.state.session_router = session_router

    # Trigger initial re-indexing (background task)
    asyncio.create_task(librarian.start_reindexing())

    # Schedule periodic re-indexing (e.g., every hour)
    scheduler.add_job(librarian.start_reindexing, 'interval', hours=1)
    logger.info("Scheduled periodic memory re-indexing (every 1 hour).")

    # If we started a NEW session (fresh install or no DB), clear focus
    if not last_session_id:
         logger.info("Fresh session detected. Clearing Focus.")
         focus_manager.clear()
    


    # Callback for RLM logging
    async def log_to_bus(level: str, message: str):
         await internal_bus.put({
             "level": level,
             "message": message,
             "source": "BRAIN"
         })

    rlm_engine = RLMEngine(
        config=config,
        gateway=gateway,
        librarian=librarian,
        focus_manager=focus_manager,
        pact_manager=pact_manager,
        tool_registry=tool_registry,
        log_callback=log_to_bus
    )

    # 5.1 Schedule Dream Cycle
    from auric.memory.chronicles import perform_dream_cycle
    
    dream_time_str = config.agents.dream_time
    try:
        hour, minute = map(int, dream_time_str.split(':'))
        scheduler.add_job(
            perform_dream_cycle, 
            'cron', 
            hour=hour, 
            minute=minute, 
            args=[audit_logger, gateway, config]
        )
        logger.info(f"Dream Cycle scheduled for {dream_time_str} daily.")
    except ValueError:
        logger.error(f"Invalid dream_time format '{dream_time_str}'. Expected HH:MM. Dream Cycle disabled.")

    # 6. Start Brain Loop & Dispatcher
    async def dispatcher_loop():
        logger.info("Message Dispatcher started.")
        while True:
            try:
                msg = await internal_bus.get()
                
                # 1. Send to Console (stdout)
                if isinstance(msg, dict):
                     level = msg.get("level", "INFO")
                     text = msg.get("message", str(msg))
                     
                     timestamp = datetime.now().strftime("%H:%M:%S")
                     color = "white"
                     if level == "ERROR": color = "bold red"
                     elif level == "WARNING": color = "yellow"
                     elif level == "THOUGHT": 
                         color = "dim cyan"
                         # Truncate thoughts for console clarity
                         lines = text.split('\n')
                         first_line = lines[0] if lines else ""
                         if len(first_line) > 100:
                             first_line = first_line[:97] + "..."
                         text_obj = Text(f"[{timestamp}] [{level}] {first_line}")
                         if len(lines) > 1 or len(text) > 100:
                              text_obj.append(" (more...)", style="dim")

                     elif level == "AGENT": color = "green"
                     elif level == "USER": color = "blue"
                     elif level == "HEARTBEAT": color = "magenta"
                     elif level == "TOOL": color = "bold yellow"
                     
                     if level != "THOUGHT": # Already created text_obj for THOUGHT
                        text_obj = Text(f"[{timestamp}] [{level}] {text}")
                     
                     text_obj.stylize(color)
                     console.print(text_obj)
                else:
                    console.print(str(msg))
                
                # 2. Store in Web Buffers & Database
                if isinstance(msg, dict):
                     level = msg.get("level")
                     text = msg.get("message", str(msg))
                     
                     # Log all dicts nicely to log buffer
                     web_log_buffer.append(f"[{level}] {text}")
                     
                     # Chat History filters
                     if level in ("USER", "AGENT", "THOUGHT", "HEARTBEAT", "TOOL"):
                          # Only show non-heartbeat messages in the Web UI Chat
                          # Heartbeats are still logged to DB below
                          if msg.get("source") != "HEARTBEAT":
                              web_chat_history.append(msg)
                              
                          # Persist to DB with current session ID
                          # Prefer session_id from message, fallback to global state
                          msg_sid = msg.get("session_id")
                          current_sid = msg_sid if msg_sid else getattr(api_app.state, "current_session_id", None)
                          await audit_logger.log_chat(role=level, content=str(text), session_id=current_sid)
                         
                else:
                    # Raw string
                    web_log_buffer.append(str(msg))
                
                internal_bus.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Dispatcher Critical Error: {e}")
                logger.error(f"Dispatcher Error: {e}")

    async def brain_loop():
        logger.info("Brain Loop started.")

        async def process_item(item):
            # Identify Message Source
            user_msg = None
            source = None
            sender_id = None
            platform = None
            session_id = item.get("session_id") if isinstance(item, dict) else None
            
            if isinstance(item, dict):
                if item.get("level") == "USER":
                     # Message from Web UI or Heartbeat
                     user_msg = item.get("message")
                     source = item.get("source", "WEB") # Respect source
                elif item.get("type") == "user_query":
                     # Message from Pact (Discord/Telegram)
                     event = item.get("event")
                     if event:
                         # Prepend User Identity
                         sender_name = event.metadata.get("author_display") or event.metadata.get("author_name") or "User"
                         user_msg = f"{sender_name}: {event.content}"
                         
                         source = "PACT"
                         platform = event.platform
                         sender_id = event.sender_id
            
            if user_msg:
                 # 0. Echo User Message to Internal Bus (for History/Console)
                 # Inject Session ID from Global State if available
                 current_sid = getattr(api_app.state, "current_session_id", None)
                 
                 # Session Management for PACTs
                 if source == "PACT" and not session_id:
                     # Use SessionRouter to get consistent but rotatable ID
                     # Context is platform:sender_id (e.g. discord:123456789)
                     context_key = f"{platform}:{sender_id}"
                     session_id = session_router.get_active_session_id(context_key)
                     
                     # If session_id is None, the context was explicitly closed.
                     # Create a brand new session (never reactivate a closed one).
                     if session_id is None:
                         logger.info(f"Context '{context_key}' was closed. Creating fresh session.")
                         session_id = session_router.start_new_session(context_key)
                     
                     # Ensure Session Exists in DB (metadata update)
                     if api_app.state.audit_logger:
                         existing = await api_app.state.audit_logger.get_session(session_id)
                         if not existing:
                             # Generate Name
                             name = f"Pact Session {session_id[:8]}"
                             if platform == "discord" and isinstance(item, dict) and "event" in item:
                                 evt = item["event"]
                                 if evt.metadata.get("is_dm"):
                                      name = f"@{evt.metadata.get('author_display')}"
                                 else:
                                      name = f"#{evt.metadata.get('channel_name')}"
                             
                             logger.info(f"Creating new PACT session: {session_id} ({name})")
                             await api_app.state.audit_logger.create_session(name=name, session_id=session_id)
                 
                 # If WEB/Other and no session, use current
                 if not session_id:
                     session_id = current_sid

                 await internal_bus.put({
                     "level": "HEARTBEAT" if source == "HEARTBEAT" else "USER",
                     "message": user_msg,
                     "source": source,
                     "session_id": session_id # Pass it along for logging
                 })

                 # Feedback to UI
                 await internal_bus.put({
                     "level": "THOUGHT",
                     "message": f"Thinking on: {user_msg}",
                     "source": "BRAIN",
                     "session_id": session_id
                 })
                 
                 logger.info(f"Thinking on: {user_msg}")
                 # Process with Engine
                 try:
                     # Extract session_id if available (from WEB) or use the one we injected
                     # session_id = item.get("session_id") if isinstance(item, dict) else None # Already handled above
                     
                     # Trigger Typing Indicator if PACT
                     if source == "PACT" and platform and sender_id:
                         asyncio.create_task(pact_manager.trigger_typing(platform, sender_id))

                     # Select model tier based on source
                     model_tier = "heartbeat_model" if source == "HEARTBEAT" else "smart_model"
                     
                     # Optimization: Lean Heartbeat Check
                     if source == "HEARTBEAT":
                         try:
                             # Use raw content if available (cleaner signal), else fallback to full message
                             check_target = item.get("heartbeat_source_content", user_msg)
                             is_necessary = await rlm_engine.check_heartbeat_necessity(check_target)
                             if not is_necessary:
                                 logger.info("🛌 Heartbeat skipped: No actionable tasks for this time.")
                                 await internal_bus.put({
                                     "level": "HEARTBEAT",
                                     "message": "🛌 Heartbeat skipped: No actionable tasks for this time.",
                                     "source": "BRAIN",
                                     "session_id": session_id
                                 })
                                 return # Skip full think cycle
                         except Exception as hb_err:
                             logger.error(f"Heartbeat Check Failed: {hb_err}. Proceeding to think anyway.")

                     response = await rlm_engine.think(user_msg, session_id=session_id, model_tier=model_tier, source=source)
                     
                     # Reply to Source
                     if source in ["WEB", "HEARTBEAT", "CLI"]: # Handle HEARTBEAT same as WEB for now logic-wise
                          await internal_bus.put({
                              "level": "AGENT",
                              "message": response,
                              "source": "BRAIN",
                              "session_id": session_id
                          })
                     elif source == "PACT":
                         adapter = pact_manager.adapters.get(platform)
                         if adapter and response:
                             await adapter.send_message(sender_id, response)
                             # Also log to internal bus for history
                             await internal_bus.put({
                                 "level": "AGENT",
                                 "message": response,
                                 "source": "BRAIN",
                                 "session_id": session_id
                             })
                             
                 except Exception as e:
                     logger.error(f"Brain Error: {e}")
                     error_msg = f"My mind is clouded: {e}"
                     
                     if source == "WEB":
                         await internal_bus.put({"level": "AGENT", "message": error_msg, "source": "BRAIN"})
                     elif source == "PACT":
                         # Attempt to report error back to user
                         try:
                             adapter = pact_manager.adapters.get(platform)
                             if adapter and sender_id:
                                 await adapter.send_message(sender_id, f"⚠️ **Error**: {e}")
                         except Exception as send_err:
                             logger.error(f"Failed to send error to PACT: {send_err}")
                         
                         # Also log to console/web
                         await internal_bus.put({
                             "level": "ERROR", 
                             "message": f"Error interacting with PACT ({platform}): {e}", 
                             "source": "BRAIN"
                         })
                 finally:
                     # Always stop typing indicator if it was a PACT
                     if source == "PACT" and platform and sender_id:
                         await pact_manager.stop_typing(platform, sender_id)

        while True:
            try:
                # Wait for command
                item = await command_bus.get()
                print(f"🧠 Brain: Received item: {item.keys() if isinstance(item, dict) else item}")
                asyncio.create_task(process_item(item))
            except asyncio.CancelledError:
                logger.info("Brain Loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Brain Loop Critical Error: {e}")
                await asyncio.sleep(1) # Backoff
    
    brain_task = asyncio.create_task(brain_loop())
    dispatcher_task = asyncio.create_task(dispatcher_loop())
    
    # Main Keep-Alive Loop
    shutdown_event = asyncio.Event()
    
    try:
        logger.info("AuricDaemon Running (Ctrl+C to stop)...")
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"Daemon crashed: {e}")
    finally:
        # 6. Graceful Shutdown
        logger.info("Shutting down AuricDaemon...")
        
        # Stop PactManager
        await pact_manager.stop()
        
        # Stop Scheduler
        scheduler.shutdown()
        
        # Stop API Server
        api_task.cancel()
        dispatcher_task.cancel()
        try:
            await api_task
            await dispatcher_task
        except asyncio.CancelledError:
            pass
            
        logger.info("Shutdown complete.")
