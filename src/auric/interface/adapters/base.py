"""
Base abstractions for platform adapters (Pacts) in OpenAuric.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

class PactEvent(BaseModel):
    """Normalized message event from any platform (Telegram, Discord, etc.)."""
    platform: str
    sender_id: str
    content: str
    reply_to_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
class BasePact(ABC):
    """Abstract base class for all platform adapters."""
    
    def __init__(self):
        self._message_handler: Optional[Callable[[PactEvent], Awaitable[None]]] = None

    @abstractmethod
    async def start(self) -> None:
        """Initialize and start the adapter."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""
        pass
        
    @abstractmethod
    async def send_message(self, target_id: str, content: str) -> None:
        """Send an outbound message to a specific user or channel."""
        pass

    async def trigger_typing(self, target_id: str) -> None:
        """Trigger a typing indicator on the target channel/user."""
        pass

    async def stop_typing(self, target_id: str) -> None:
        """Stop the typing indicator on the target channel/user."""
        pass

    def on_message(self, callback: Callable[[PactEvent], Awaitable[None]]) -> None:
        """Register a callback to handle incoming messages."""
        self._message_handler = callback

    async def _emit(self, event: PactEvent) -> None:
        """Internal helper to trigger the registered message callback."""
        if self._message_handler:
            await self._message_handler(event)

    def get_tools_definition(self) -> str:
        """Return a markdown description of the tools provided by this pact."""
        return ""

    def get_tool_names(self) -> List[str]:
        """Return a list of tool names handled by this pact."""
        return []

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """Return a list of JSON schemas for the tools provided by this pact."""
        return []

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Execute a tool provided by this pact."""
        raise NotImplementedError(f"Tool {tool_name} not implemented in {self.__class__.__name__}")
