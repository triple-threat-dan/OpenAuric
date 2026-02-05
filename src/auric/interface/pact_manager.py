import asyncio
import logging
from typing import Dict, Optional, List

from auric.core.config import AuricConfig
from auric.core.database import AuditLogger
from auric.interface.adapters.base import BasePact, PactEvent
from auric.interface.adapters.telegram import TelegramPact
from auric.interface.adapters.discord import DiscordPact

logger = logging.getLogger("auric.pact.manager")

class PactManager:
    """
    Omni-Channel Manager that unifies Telegram, Discord, and other inputs.
    Handles HITL (Human-in-the-Loop) Resume logic.
    """
    def __init__(self, config: AuricConfig, audit_logger: AuditLogger, event_bus: asyncio.Queue):
        self.config = config
        self.audit = audit_logger
        self.bus = event_bus
        self.adapters: Dict[str, BasePact] = {}

    async def start(self) -> None:
        """
        Initialize and start enabled adapters.
        """
        # Telegram
        if self.config.pacts.telegram.enabled and self.config.pacts.telegram.token:
            telegram = TelegramPact(token=self.config.pacts.telegram.token)
            telegram.on_message(self.handle_message)
            self.adapters["telegram"] = telegram
            await telegram.start()

        # Discord
        if self.config.pacts.discord.enabled and self.config.pacts.discord.token:
            discord = DiscordPact(token=self.config.pacts.discord.token)
            discord.on_message(self.handle_message)
            self.adapters["discord"] = discord
            await discord.start()

        if not self.adapters:
            logger.warning("No Pacts enabled. Agent is lonely.")

    async def stop(self) -> None:
        """
        Stop all adapters.
        """
        for name, adapter in self.adapters.items():
            await adapter.stop()
        self.adapters.clear()

    async def handle_message(self, event: PactEvent) -> None:
        """
        Central ingestion point for all platform messages.
        """
        logger.info(f"Incoming PactEvent from {event.platform}: {event.content[:50]}")

        # 1. Check for Resume Logic (HITL)
        pending_task = await self.audit.get_pending_approval_task()
        
        if pending_task:
            if self._is_approval(event.content):
                logger.info(f"User approved task {pending_task.id} via {event.platform}")
                
                # Update DB
                await self.audit.update_status(pending_task.id, "RUNNING")
                
                # Signal Daemon/RLM to wake up
                # We assume the event bus carries simple dicts or objects.
                # 'resume_signal' indicates a HITL resolution.
                await self.bus.put({
                    "type": "resume_signal",
                    "task_id": pending_task.id,
                    "platform": event.platform,
                    "user_input": event.content
                })
                
                # Notify user back (Ack)
                adapter = self.adapters.get(event.platform)
                if adapter:
                    await adapter.send_message(event.sender_id, "✅ Deployment approved. Resuming...")
                
                return
            else:
                 # Pending task exists, but user said something else. 
                 # Could be a denial or just chat. 
                 # For now, if it's explicitly "no" or "stop", we might want to cancel.
                 if self._is_denial(event.content):
                     logger.info(f"User denied task {pending_task.id} via {event.platform}")
                     await self.audit.update_status(pending_task.id, "CANCELLED") # Or FAILED
                     await self.bus.put({
                        "type": "cancel_signal",
                        "task_id": pending_task.id
                     })
                     adapter = self.adapters.get(event.platform)
                     if adapter:
                        await adapter.send_message(event.sender_id, "🛑 Task cancelled.")
                     return

        # 2. Standard User Query (No pending task or unrelated message)
        # Push to queue for the brain to process
        await self.bus.put({
            "type": "user_query",
            "event": event
        })

    def _is_approval(self, text: str) -> bool:
        """
        Simple heuristic for approval.
        """
        text = text.lower().strip()
        positive_keywords = ["yes", "approve", "proceed", "go", "confirm", "ok", "run"]
        return any(keyword in text for keyword in positive_keywords)

    def _is_denial(self, text: str) -> bool:
        """
        Simple heuristic for denial.
        """
        text = text.lower().strip()
        negative_keywords = ["no", "stop", "cancel", "deny", "abort", "wait"]
        return any(keyword in text for keyword in negative_keywords)
