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
from typing import Optional
from pathlib import Path

from textual.app import App
from uvicorn import Config, Server
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .database import AuditLogger
from auric.interface.tui.app import AuricTUI
from auric.interface.server.routes import router as dashboard_router

logger = logging.getLogger("auric.daemon")

async def run_daemon(tui_app: Optional[App], api_app: FastAPI) -> None:
    """
    Entry point for the OpenAuric Daemon.
    
    Args:
        tui_app: The Textual App instance for the TUI (optional, but usually AuricTUI).
        api_app: The FastAPI instance for the REST API.
    """
    # 0. Load Configuration
    config = load_config()
    logger.info("AuricDaemon starting up...")

    # 1. Setup Internal Bus
    # This queue will carry "thoughts", logs, and status updates
    # from the background processes (API, Scheduler, Agents) to the UI.
    event_bus: asyncio.Queue = asyncio.Queue()
    
    # Inject event bus into API state
    api_app.state.event_bus = event_bus

    # 1.1 Configure API App (Routes & Static)
    # Mount static files
    static_path = Path(__file__).parent.parent / "interface" / "server" / "static"
    if not static_path.exists():
        logger.warning(f"Static path {static_path} not found. Creating...")
        static_path.mkdir(parents=True, exist_ok=True)
        
    api_app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")
    api_app.include_router(dashboard_router)

    # 2. Setup Scheduler (Heartbeat & Dream Cycle)
    scheduler = AsyncIOScheduler()
    
    # Placeholder: Add heartbeat job if configured
    if config.agents.defaults.heartbeat.enabled:
        # scheduler.add_job(heartbeat_task, 'interval', minutes=30) 
        pass

    scheduler.start()
    logger.info("Scheduler started.")

    # 3. Setup Database & Audit Logger
    audit_logger = AuditLogger()
    await audit_logger.init_db()
    
    # 3.1 Setup Pact Manager (Omni-Channel)
    from auric.interface.pact_manager import PactManager
    pact_manager = PactManager(config, audit_logger, event_bus)
    await pact_manager.start()
    logger.info("PactManager started.")

    # 4. Setup FastAPI (Uvicorn)
    # We must run Uvicorn manually to keep it in our existing asyncio loop.
    
    uvi_config = Config(
        app=api_app, 
        host=config.gateway.host,
        port=config.gateway.port, 
        loop="asyncio",
        log_level="info" if logger.level <= logging.INFO else "warning"
    )
    server = Server(uvi_config)
    
    # Launch server as a background task
    api_task = asyncio.create_task(server.serve())
    logger.info(f"API Server starting on {config.gateway.host}:{config.gateway.port}")

    # 5. Run the TUI
    # If tui_app is None, we instantiate our default AuricTUI
    if tui_app is None:
        focus_path = Path("~/.auric/grimoire/FOCUS.md").expanduser()
        tui_app = AuricTUI(event_bus=event_bus, focus_file=focus_path)
    
    try:
        logger.info("Launching TUI...")
        await tui_app.run_async()
    except Exception as e:
        logger.critical(f"TUI crashed: {e}")
    finally:
        # 6. Graceful Shutdown
        logger.info("Shutting down AuricDaemon...")
        
        # Stop PactManager
        await pact_manager.stop()
        
        # Stop Scheduler
        scheduler.shutdown()
        
        # Stop API Server
        api_task.cancel()
        try:
            await api_task
        except asyncio.CancelledError:
            pass
            
        logger.info("Shutdown complete.")
