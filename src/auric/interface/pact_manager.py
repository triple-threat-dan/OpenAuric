import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from auric.core.config import AuricConfig
from auric.core.database import AuditLogger
from auric.interface.adapters.base import BasePact, PactEvent
from auric.interface.adapters.discord import DiscordPact
from auric.interface.adapters.telegram import TelegramPact

logger = logging.getLogger("auric.pact.manager")

class PactManager:
    """
    Omni-Channel Manager that unifies Telegram, Discord, and other inputs.
    Handles HITL (Human-in-the-Loop) Resume logic.
    """
    
    _APPROVAL_KEYWORDS = frozenset({"yes", "approve", "proceed", "go", "confirm", "ok", "run"})
    _DENIAL_KEYWORDS = frozenset({"no", "stop", "cancel", "deny", "abort", "wait"})

    def __init__(
        self, 
        config: AuricConfig, 
        audit_logger: AuditLogger, 
        command_bus: asyncio.Queue, 
        internal_bus: asyncio.Queue, 
        session_router: Any = None
    ):
        self.config = config
        self.audit = audit_logger
        self.command_bus = command_bus
        self.internal_bus = internal_bus
        self.session_router = session_router
        self.adapters: Dict[str, BasePact] = {}
        
        # Performance caches
        self._tool_map: Dict[str, BasePact] = {}
        self._cached_schemas: List[Dict[str, Any]] = []
        self._cached_tool_names: Set[str] = set()

    async def start(self) -> None:
        """Initialize and start enabled adapters."""
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
            return

        # Build performance caches
        self._tool_map = {}
        self._cached_schemas = []
        self._cached_tool_names = set()
        
        for adapter in self.adapters.values():
            adapter_tool_names = adapter.get_tool_names()
            self._cached_tool_names.update(adapter_tool_names)
            self._cached_schemas.extend(adapter.get_tools_schema())
            
            for tool_name in adapter_tool_names:
                self._tool_map[tool_name] = adapter

    async def stop(self) -> None:
        """Stop all adapters."""
        for adapter in self.adapters.values():
            await adapter.stop()
        self.adapters.clear()
        self._tool_map.clear()
        self._cached_schemas.clear()
        self._cached_tool_names.clear()

    async def handle_message(self, event: PactEvent) -> None:
        """Central ingestion point for all platform messages."""
        logger.info(f"Incoming PactEvent from {event.platform}: {event.content[:50]}")

        # 1. Check for Resume Logic (HITL)
        pending_task = await self.audit.get_pending_approval_task()
        
        if pending_task:
            if self._is_approval(event.content):
                logger.info(f"User approved task {pending_task.id} via {event.platform}")
                await self.audit.update_status(pending_task.id, "RUNNING")
                await self.command_bus.put({
                    "type": "resume_signal",
                    "task_id": pending_task.id,
                    "platform": event.platform,
                    "user_input": event.content
                })
                
                if adapter := self.adapters.get(event.platform):
                    await adapter.send_message(event.sender_id, "✅ Deployment approved. Resuming...")
                return

            if self._is_denial(event.content):
                logger.info(f"User denied task {pending_task.id} via {event.platform}")
                await self.audit.update_status(pending_task.id, "CANCELLED")
                await self.command_bus.put({
                    "type": "cancel_signal",
                    "task_id": pending_task.id
                })
                
                if adapter := self.adapters.get(event.platform):
                    await adapter.send_message(event.sender_id, "🛑 Task cancelled.")
                return

        # 2. Standard User Query
        await self.command_bus.put({
            "type": "user_query",
            "event": event
        })

    async def trigger_typing(self, platform: str, target_id: str) -> None:
        """Triggers typing indicator on the specified platform."""
        if adapter := self.adapters.get(platform):
            await adapter.trigger_typing(target_id)

    async def stop_typing(self, platform: str, target_id: str) -> None:
        """Stops typing indicator on the specified platform."""
        if adapter := self.adapters.get(platform):
            await adapter.stop_typing(target_id)

    def get_all_tools_definitions(self) -> str:
        """Aggregates tool definitions from all enabled pacts."""
        definitions = [
            defs for adapter in self.adapters.values() 
            if (defs := adapter.get_tools_definition())
        ]
        return "\n\n".join(definitions)

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Aggregates JSON schemas from all enabled pacts."""
        return self._cached_schemas if self._cached_schemas else []

    def get_tool_names(self) -> Set[str]:
        """Returns the set of all tool names available across all enabled pacts."""
        return self._cached_tool_names

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Executes a tool by name across all active pacts."""
        adapter = self._tool_map.get(tool_name)
        if not adapter:
            raise ValueError(f"Tool {tool_name} not found in any active pact.")

        logger.info(f"Executing tool {tool_name}")
        result = await adapter.execute_tool(tool_name, args)
        
        # Intercept outbound messaging to sync session history
        if tool_name in ("discord_send_dm", "discord_send_channel_message"):
            target_id = args.get("user_id") or args.get("channel_id")
            content = args.get("content")
            
            if target_id and content and self.session_router and self.audit:
                # Find the adapter name for this tool (already have adapter object)
                # We need to find the key in self.adapters that maps to this adapter
                adapter_name = next((k for k, v in self.adapters.items() if v == adapter), "unknown")
                context_key = f"{adapter_name}:{target_id}"
                
                if target_session_id := self.session_router.get_active_session_id(context_key):
                     logger.info(f"Syncing outbound tool call {tool_name} to session {target_session_id}")
                     await self.audit.log_chat(role="AGENT", content=content, session_id=target_session_id)
                             
        return result

    def _is_approval(self, text: str) -> bool:
        """Simple heuristic for approval."""
        content_lower = text.lower().strip()
        return any(k in content_lower for k in self._APPROVAL_KEYWORDS)

    def _is_denial(self, text: str) -> bool:
        """Simple heuristic for denial."""
        content_lower = text.lower().strip()
        return any(k in content_lower for k in self._DENIAL_KEYWORDS)
