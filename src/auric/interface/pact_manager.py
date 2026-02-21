import asyncio
import logging
from typing import Dict, Optional, List, Any

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
    def __init__(self, config: AuricConfig, audit_logger: AuditLogger, command_bus: asyncio.Queue, event_bus: asyncio.Queue):
        self.config = config
        self.audit = audit_logger
        self.command_bus = command_bus
        self.event_bus = event_bus
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
            discord = DiscordPact(
                token=self.config.pacts.discord.token,
                allowed_channels=self.config.pacts.discord.allowed_channels,
                allowed_users=self.config.pacts.discord.allowed_users,
                agent_name=self.config.agents.name,
                api_port=self.config.gateway.port,
                bot_loop_limit=self.config.pacts.discord.bot_loop_limit
            )
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
                # 'resume_signal' indicates a HITL resolution.
                await self.command_bus.put({
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
                     await self.command_bus.put({
                        "type": "cancel_signal",
                        "task_id": pending_task.id
                     })
                     adapter = self.adapters.get(event.platform)
                     if adapter:
                        await adapter.send_message(event.sender_id, "🛑 Task cancelled.")
                     return

        # 2. Standard User Query (No pending task or unrelated message)
        # Push to queue for the brain to process
        # 2. Standard User Query (No pending task or unrelated message)
        # Push to queue for the brain to process
        await self.command_bus.put({
            "type": "user_query",
            "event": event
        })

    
    # ==========================
    # Tool Abstraction Methods
    # ==========================

    def get_all_tools_definitions(self) -> str:
        """
        Aggregates tool definitions from all enabled pacts.
        """
        definitions = []
        for name, adapter in self.adapters.items():
            defs = adapter.get_tools_definition()
            if defs:
                definitions.append(defs)
        return "\n\n".join(definitions)

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
         routes execution to the correct adapter.
        """
        # Linear search for now, could optimize with a map
        for name, adapter in self.adapters.items():
            if tool_name in adapter.get_tool_names():
                try:
                    return await adapter.execute_tool(tool_name, args)
                except Exception as e:
                    logger.error(f"Error executing tool {tool_name} on pact {name}: {e}")
                    raise
        
        raise ValueError(f"Tool {tool_name} not found in any active pact.")

    async def trigger_typing(self, platform: str, target_id: str) -> None:
        """
        Triggers typing indicator on the specified platform.
        """
        adapter = self.adapters.get(platform)
        if adapter:
            await adapter.trigger_typing(target_id)

    async def stop_typing(self, platform: str, target_id: str) -> None:
        """
        Stops typing indicator on the specified platform.
        """
        adapter = self.adapters.get(platform)
        if adapter:
            await adapter.stop_typing(target_id)

    # ==========================
    # Tool Abstraction Methods
    # ==========================

    def get_all_tools_definitions(self) -> str:
        """
        Aggregates tool definitions from all enabled pacts.
        """
        definitions = []
        for name, adapter in self.adapters.items():
            defs = adapter.get_tools_definition()
            if defs:
                definitions.append(defs)
        return "\n\n".join(definitions)

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        Aggregates JSON schemas from all enabled pacts.
        """
        schemas = []
        for name, adapter in self.adapters.items():
            schemas.extend(adapter.get_tools_schema())
        return schemas

    def get_tool_names(self) -> set:
        """
        Returns the set of all tool names available across all enabled pacts.
        """
        names = set()
        for name, adapter in self.adapters.items():
            names.update(adapter.get_tool_names())
        return names

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        # Linear search for now, could optimize with a map
        for name, adapter in self.adapters.items():
            if tool_name in adapter.get_tool_names():
                logger.info(f"Executing tool {tool_name} via {name} pact")
                return await adapter.execute_tool(tool_name, args)
        
        raise ValueError(f"Tool {tool_name} not found in any active pact.")

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
