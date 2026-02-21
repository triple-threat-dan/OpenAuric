from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, Optional, Dict, Any, Awaitable

from pydantic import BaseModel, Field

class PactEvent(BaseModel):
    """
    Normalized message event from any platform (Telegram, Discord, CLI).
    """
    platform: str
    sender_id: str
    content: str
    reply_to_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
class BasePact(ABC):
    """
    Abstract base class for platform adapters.
    """
    
    def __init__(self):
        self._message_handler: Optional[Callable[[PactEvent], Awaitable[None]]] = None

    @abstractmethod
    async def start(self) -> None:
        """
        Start the adapter (e.g., start polling or connecting to websocket).
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """
        Stop the adapter.
        """
        pass
        
    @abstractmethod
    async def send_message(self, target_id: str, content: str) -> None:
        """
        Send an outbound message to a specific user/channel.
        """
        pass

    async def trigger_typing(self, target_id: str) -> None:
        """
        Trigger a typing indicator on the target channel/user.
        Default implementation is a no-op.
        """
        pass

    async def stop_typing(self, target_id: str) -> None:
        """
        Stop the typing indicator on the target channel/user.
        Default implementation is a no-op.
        """
        pass

    def on_message(self, callback: Callable[[PactEvent], Awaitable[None]]) -> None:
        """
        Register the callback function to handle incoming messages.
        """
        self._message_handler = callback

    async def _emit(self, event: PactEvent) -> None:
        """
        Internal helper to trigger the registered callback.
        """
        if self._message_handler:
            await self._message_handler(event)

    def get_tools_definition(self) -> str:
        """
        Returns the markdown explanation of tools this pact provides.
        Optional override for pacts without tools.
        """
        return ""

    def get_tool_names(self) -> list[str]:
        """
        Returns a list of tool names this pact handles (for routing).
        e.g. ['discord_send_dm', 'discord_send_channel_message']
        """
        return []

    def get_tools_schema(self) -> list[Dict[str, Any]]:
        """
        Returns a list of JSON schemas for the tools provided by this pact.
        """
        return []

    async def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Executes a tool if this pact owns it.
        """
        raise NotImplementedError(f"Tool {tool_name} not implemented in {self.__class__.__name__}")
