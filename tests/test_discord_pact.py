import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio
from datetime import datetime
import sys

# Define dummy classes to replace discord ones
class DummyClient:
    def __init__(self, *args, **kwargs):
        self.user = MagicMock()
        self.user.id = "BOT_ID"

class DummyMessage:
    def __init__(self):
        self.author = MagicMock()
        self.channel = MagicMock()
        self.content = ""
        self.created_at = datetime.now()
        self.reference = None

# Mock the module
mock_discord = MagicMock()
mock_discord.Client = DummyClient
mock_discord.Message = DummyMessage
mock_discord.DMChannel = type('DMChannel', (), {}) # Dummy type

sys.modules["discord"] = mock_discord

from auric.interface.adapters.discord import DiscordPact, AuricDiscordClient

class TestDiscordPact(unittest.TestCase):
    def setUp(self):
        self.pact = DiscordPact(token="fake_token", allowed_channels=["123"], allowed_users=["456"])
        self.pact.client = AuricDiscordClient(self.pact)
        self.pact._emit = AsyncMock()

    def test_on_message_ignore_self(self):
        # Setup
        message = MagicMock()
        message.author = self.pact.client.user
        
        # Action
        asyncio.run(self.pact.client.on_message(message))
        
        # Assert
        self.pact._emit.assert_not_called()

    def test_on_message_allowed_user_and_channel(self):
        # Setup
        message = MagicMock()
        message.author.id = "456" # Allowed
        message.channel.id = "123" # Allowed
        message.content = "Hello"
        message.created_at = datetime.now()
        
        # Action
        asyncio.run(self.pact.client.on_message(message))
        
        # Assert
        self.pact._emit.assert_called_once()

    def test_on_message_blocked_user(self):
        # Setup
        message = MagicMock()
        message.author.id = "999" # Not Allowed
        message.channel.id = "123" # Allowed
        
        # Action
        asyncio.run(self.pact.client.on_message(message))
        
        # Assert
        self.pact._emit.assert_not_called()

    def test_on_message_blocked_channel(self):
        # Setup
        message = MagicMock()
        message.author.id = "456" # Allowed
        message.channel.id = "999" # Not Allowed
        
        # Action
        asyncio.run(self.pact.client.on_message(message))
        
        # Assert
        self.pact._emit.assert_not_called()

    def test_send_channel_message(self):
        # Setup
        self.pact.client.is_ready = MagicMock(return_value=True)
        channel = AsyncMock()
        self.pact.client.get_channel = MagicMock(return_value=channel)
        
        # Action
        asyncio.run(self.pact.send_channel_message("123", "Hello Channel"))
        
        # Assert
        channel.send.assert_called_with("Hello Channel")

    def test_send_dm(self):
        # Setup
        self.pact.client.is_ready = MagicMock(return_value=True)
        user = AsyncMock()
        self.pact.client.fetch_user = AsyncMock(return_value=user)
        
        # Action
        asyncio.run(self.pact.send_dm("456", "Hello DM"))
        
        # Assert
        self.pact.client.fetch_user.assert_called_with(456)
        user.send.assert_called_with("Hello DM")

    async def test_execute_tool_success(self):
         # Setup
        self.pact.client.is_ready = MagicMock(return_value=True)
        user = AsyncMock()
        self.pact.client.fetch_user = AsyncMock(return_value=user)
        
        # Action
        result = await self.pact.execute_tool("discord_send_dm", {"user_id": "456", "content": "Hello"})
        
        # Assert
        self.assertEqual(result, "Message sent.")
        user.send.assert_called_with("Hello")

    async def test_execute_tool_unknown(self):
        with self.assertRaises(NotImplementedError):
            await self.pact.execute_tool("unknown_tool", {})

    def test_get_tool_names(self):
        names = self.pact.get_tool_names()
        self.assertIn("discord_send_dm", names)
        self.assertIn("discord_send_channel_message", names)

if __name__ == "__main__":
    unittest.main()
