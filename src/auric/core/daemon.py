"""
The AuricDaemon: Core orchestration engine for OpenAuric.

This module is responsible for initializing and managing the lifecycle of:
1. The Terminal User Interface (Textual) - Currently Disabled
2. The REST API (FastAPI/Uvicorn)
3. The Task Scheduler (APScheduler)

It ensures all components run within the same asyncio event loop,
sharing memory and state.
"""

import asyncio
import logging
import os
import secrets
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, Dict, Optional
from uuid import uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from rich.console import Console
from rich.text import Text
from textual.app import App
from uvicorn import Config, Server

from auric.core.bootstrap import ensure_workspace
from auric.core.config import AURIC_ROOT, ConfigLoader, load_config
from auric.core.database import AuditLogger
from auric.core.session_router import SessionRouter
from auric.interface.server.routes import router as dashboard_router

logger = logging.getLogger("auric.daemon")
console = Console()

# Module-level constants for performance
STATIC_PATH = Path(__file__).parent.parent / "interface" / "server" / "static"

class EndpointFilter(logging.Filter):
    """Filter out health checks and status polling from access logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/status" not in msg and "/api/sessions" not in msg

def parse_interval(interval_str: str) -> Dict[str, int]:
    """Parses a heartbeat interval string into APScheduler kwargs."""
    try:
        if interval_str.endswith("m"):
            return {"minutes": int(interval_str[:-1])}
        if interval_str.endswith("h"):
            return {"hours": int(interval_str[:-1])}
        if interval_str.endswith("s"):
            return {"seconds": int(interval_str[:-1])}
    except ValueError:
        pass
    
    logger.warning(f"Invalid heartbeat interval '{interval_str}', defaulting to 30m")
    return {"minutes": 30}

async def run_daemon(tui_app: Optional[App], api_app: FastAPI) -> None:
    """
    Entry point for the OpenAuric Daemon.
    
    Args:
        tui_app: The Textual App instance for the TUI (optional).
        api_app: The FastAPI instance for the REST API.
    """
    config = load_config()
    logger.info(f"OpenAuric Root: {AURIC_ROOT}")
    
    # Security: Ensure Web UI Token exists
    if not config.gateway.web_ui_token:
        token = secrets.token_urlsafe(32)
        config.gateway.web_ui_token = token
        ConfigLoader.save(config)
        logger.warning(f"Generated new Web UI Token: {token}")
        console.print(f"[bold yellow]Generated new Web UI Token: {token}[/bold yellow]")
        console.print("Use 'auric token' to retrieve it later.")

    logger.info(f"Starting Auric Daemon (PID {os.getpid()})...")
    
    ensure_workspace()

    # Setup Internal Buses
    command_bus: asyncio.Queue = asyncio.Queue()
    internal_bus: asyncio.Queue = asyncio.Queue()
    
    web_chat_history: Deque[Dict[str, Any]] = deque(maxlen=50)
    web_log_buffer: Deque[str] = deque(maxlen=100)
    
    # Inject state into API app
    api_app.state.command_bus = command_bus
    api_app.state.web_chat_history = web_chat_history
    api_app.state.web_log_buffer = web_log_buffer
    api_app.state.config = config
    
    api_app.include_router(dashboard_router)

    @api_app.post("/spells/reload")
    async def reload_spells():
        try:
            registry = getattr(api_app.state, "tool_registry", None)
            if registry:
                registry.load_spells()
                return {"status": "ok", "count": len(registry._spells)}
            return {"status": "error", "message": "ToolRegistry not initialized yet."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Mount static files
    if not STATIC_PATH.exists():
        logger.warning(f"Static path {STATIC_PATH} not found. Creating...")
        STATIC_PATH.mkdir(parents=True, exist_ok=True)
    api_app.mount("/", StaticFiles(directory=str(STATIC_PATH), html=True, check_dir=False), name="static")

    # Setup Infrastructure
    scheduler = AsyncIOScheduler()
    audit_logger = AuditLogger()
    await audit_logger.init_db()
    api_app.state.audit_logger = audit_logger

    # Load Last Session
    last_session_id = await audit_logger.get_last_active_session_id()
    current_session_id = last_session_id or str(uuid4())
    api_app.state.current_session_id = current_session_id
    logger.info(f"{'Resuming' if last_session_id else 'Starting new'} session: {current_session_id}")

    # Heartbeat Initialization
    from auric.core.heartbeat import HeartbeatManager, run_heartbeat_task
    HeartbeatManager(audit_logger)

    if config.agents.defaults.heartbeat.enabled:
        kwargs = parse_interval(config.agents.defaults.heartbeat.interval)
        scheduler.add_job(run_heartbeat_task, 'interval', args=[command_bus], **kwargs)
        logger.info(f"Heartbeat scheduled every {config.agents.defaults.heartbeat.interval}.")

    scheduler.start()
    logger.info("Scheduler started.")

    session_router = SessionRouter(AURIC_ROOT / "active_sessions.json")

    from auric.interface.pact_manager import PactManager
    pact_manager = PactManager(config, audit_logger, command_bus, internal_bus, session_router)
    await pact_manager.start()
    logger.info("PactManager started.")

    # Setup API Server (Uvicorn)
    uvi_config = Config(
        app=api_app, 
        host=config.gateway.host,
        port=config.gateway.port, 
        loop="asyncio",
        log_level="info" if logger.level <= logging.INFO else "warning",
    )
    server = Server(uvi_config)
    server.install_signal_handlers = lambda: None

    async def safe_serve():
        try:
            await server.serve()
        except SystemExit:
            logger.error("Uvicorn failed to start (likely port in use).")
        except asyncio.CancelledError:
            pass
            
    access_logger = logging.getLogger("uvicorn.access")
    if config.gateway.disable_access_log:
        access_logger.disabled = True
    else:
        access_logger.addFilter(EndpointFilter())
    
    api_task = asyncio.create_task(safe_serve())
    logger.info(f"API Server starting on {config.gateway.host}:{config.gateway.port}")

    # Brain Components Initialization
    from auric.brain.llm_gateway import LLMGateway
    from auric.brain.rlm import RLMEngine
    from auric.memory.focus_manager import FocusManager
    from auric.memory.librarian import GrimoireLibrarian
    from auric.spells.tool_registry import ToolRegistry

    gateway = LLMGateway(config, audit_logger=audit_logger)
    librarian = GrimoireLibrarian()
    librarian.start()
    
    focus_path = AURIC_ROOT / "memories" / "FOCUS.md"
    focus_manager = FocusManager(focus_path)
    
    tool_registry = ToolRegistry(config, librarian=librarian, audit_logger=audit_logger, session_router=session_router)
    
    # Inject dependencies into state
    api_app.state.tool_registry = tool_registry
    api_app.state.focus_manager = focus_manager
    api_app.state.gateway = gateway
    api_app.state.session_router = session_router

    asyncio.create_task(librarian.start_reindexing())
    scheduler.add_job(librarian.start_reindexing, 'interval', hours=1)
    logger.info("Scheduled periodic memory re-indexing (every 1 hour).")

    if not last_session_id:
         logger.info("Fresh session detected. Clearing Focus.")
         focus_manager.clear()

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

    # Schedule Dream Cycle
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
        logger.error(f"Invalid dream_time format '{dream_time_str}'. Expected HH:MM.")

    async def dispatcher_loop():
        logger.info("Message Dispatcher started.")
        while True:
            try:
                msg = await internal_bus.get()
                
                if isinstance(msg, dict):
                    level = msg.get("level", "INFO")
                    text = msg.get("message", str(msg))
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    # Console styling
                    colors = {
                        "ERROR": "bold red",
                        "WARNING": "yellow",
                        "AGENT": "green",
                        "USER": "blue",
                        "HEARTBEAT": "magenta",
                        "TOOL": "bold yellow",
                        "THOUGHT": "dim cyan"
                    }
                    color = colors.get(level, "white")

                    if level == "THOUGHT":
                        first_line = text.split('\n')[0]
                        if len(first_line) > 100:
                            first_line = first_line[:97] + "..."
                        text_obj = Text(f"[{timestamp}] [{level}] {first_line}")
                        if '\n' in text or len(text) > 100:
                             text_obj.append(" (more...)", style="dim")
                    else:
                        text_obj = Text(f"[{timestamp}] [{level}] {text}")
                    
                    text_obj.stylize(color)
                    console.print(text_obj)
                    
                    # Log Buffer & Persistence
                    web_log_buffer.append(f"[{level}] {text}")
                    if level in ("USER", "AGENT", "THOUGHT", "HEARTBEAT", "TOOL"):
                        if msg.get("source") != "HEARTBEAT":
                            web_chat_history.append(msg)
                            
                        msg_sid = msg.get("session_id")
                        target_sid = msg_sid or getattr(api_app.state, "current_session_id", None)
                        await audit_logger.log_chat(role=level, content=str(text), session_id=target_sid)
                else:
                    console.print(str(msg))
                    web_log_buffer.append(str(msg))
                
                internal_bus.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Dispatcher Error: {e}")

    async def brain_loop():
        logger.info("Brain Loop started.")

        async def process_item(item):
            if not isinstance(item, dict):
                return

            user_msg = None
            source = item.get("source", "WEB")
            platform = None
            sender_id = None
            session_id = item.get("session_id")
            
            if item.get("level") == "USER":
                user_msg = item.get("message")
            elif item.get("type") == "user_query":
                event = item.get("event")
                if event:
                    sender_name = event.metadata.get("author_display") or event.metadata.get("author_name") or "User"
                    user_msg = f"{sender_name}: {event.content}"
                    source = "PACT"
                    platform = event.platform
                    sender_id = event.sender_id
            
            if not user_msg:
                return

            # Session Management
            if source == "PACT" and not session_id:
                context_key = f"{platform}:{sender_id}"
                session_id = session_router.get_active_session_id(context_key)
                if session_id is None:
                    session_id = session_router.start_new_session(context_key)
                
                if not await audit_logger.get_session(session_id):
                    name = f"Pact Session {session_id[:8]}"
                    if platform == "discord" and "event" in item:
                        evt = item["event"]
                        name = f"@{evt.metadata.get('author_display')}" if evt.metadata.get("is_dm") else f"#{evt.metadata.get('channel_name')}"
                    await audit_logger.create_session(name=name, session_id=session_id)
            
            session_id = session_id or getattr(api_app.state, "current_session_id", None)

            # Echo to dispatcher
            await internal_bus.put({
                "level": "HEARTBEAT" if source == "HEARTBEAT" else "USER",
                "message": user_msg,
                "source": source,
                "session_id": session_id
            })

            await internal_bus.put({
                "level": "THOUGHT",
                "message": f"Thinking on: {user_msg}",
                "source": "BRAIN",
                "session_id": session_id
            })
            
            logger.info(f"Thinking on: {user_msg}")
            
            try:
                if source == "PACT" and platform and sender_id:
                    asyncio.create_task(pact_manager.trigger_typing(platform, sender_id))

                model_tier = "heartbeat_model" if source == "HEARTBEAT" else "smart_model"
                
                if source == "HEARTBEAT":
                    try:
                        check_target = item.get("heartbeat_source_content", user_msg)
                        if not await rlm_engine.check_heartbeat_necessity(check_target):
                            logger.info("🛌 Heartbeat skipped: No actionable tasks.")
                            await internal_bus.put({
                                "level": "HEARTBEAT",
                                "message": "🛌 Heartbeat skipped: No actionable tasks.",
                                "source": "BRAIN",
                                "session_id": session_id
                            })
                            return
                    except Exception as hb_err:
                        logger.error(f"Heartbeat Check Failed: {hb_err}")

                response = await rlm_engine.think(user_msg, session_id=session_id, model_tier=model_tier, source=source)
                
                if response:
                    if source in ["WEB", "HEARTBEAT", "CLI"]:
                        await internal_bus.put({
                            "level": "AGENT",
                            "message": response,
                            "source": "BRAIN",
                            "session_id": session_id
                        })
                    elif source == "PACT":
                        adapter = pact_manager.adapters.get(platform)
                        if adapter:
                            await adapter.send_message(sender_id, response)
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
                    try:
                        adapter = pact_manager.adapters.get(platform)
                        if adapter and sender_id:
                            await adapter.send_message(sender_id, f"⚠️ **Error**: {e}")
                    except Exception:
                        pass
                    await internal_bus.put({"level": "ERROR", "message": f"PACT ({platform}) error: {e}", "source": "BRAIN"})
            finally:
                if source == "PACT" and platform and sender_id:
                    await pact_manager.stop_typing(platform, sender_id)

        while True:
            try:
                item = await command_bus.get()
                await process_item(item)
                await internal_bus.join()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Brain Loop Error: {e}")
                await asyncio.sleep(1)
    
    brain_task = asyncio.create_task(brain_loop())
    dispatcher_task = asyncio.create_task(dispatcher_loop())
    
    shutdown_event = asyncio.Event()
    
    try:
        logger.info("AuricDaemon Running (Ctrl+C to stop)...")
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.critical(f"Daemon crashed: {e}")
    finally:
        logger.info("Shutting down AuricDaemon...")
        await pact_manager.stop()
        scheduler.shutdown()
        api_task.cancel()
        dispatcher_task.cancel()
        brain_task.cancel()
        try:
            await asyncio.gather(api_task, dispatcher_task, brain_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        logger.info("Shutdown complete.")
