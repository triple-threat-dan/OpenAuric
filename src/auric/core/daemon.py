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

from textual.app import App
from uvicorn import Config, Server
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from .config import load_config
from .database import AuditLogger

logger = logging.getLogger("auric.daemon")

async def run_daemon(tui_app: App, api_app: FastAPI) -> None:
    """
    Entry point for the OpenAuric Daemon.
    
    Args:
        tui_app: The Textual App instance for the TUI.
        api_app: The FastAPI instance for the REST API.
    """
    # 0. Load Configuration
    config = load_config()
    logger.info("AuricDaemon starting up...")

    # 1. Setup Internal Bus
    # This queue will carry "thoughts", logs, and status updates
    # from the background processes (API, Scheduler, Agents) to the UI.
    event_bus: asyncio.Queue = asyncio.Queue()
    
    # TODO: Inject this event_bus into the apps if they need it
    # tui_app.context['event_bus'] = event_bus 
    # api_app.state.event_bus = event_bus

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
    # standard uvicorn.run() blocks and creates a new loop usually.
    
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
    # Textual's run_async() is a blocking call that drives the main loop.
    # It must be awaited last.
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
        # Uvicorn server.serve() checks for cancellation or should be cancelled
        # server.should_exit = True # Alternative triggers
        api_task.cancel()
        try:
            await api_task
        except asyncio.CancelledError:
            pass
            
        logger.info("Shutdown complete.")
