import unittest
from unittest.mock import MagicMock, AsyncMock
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
        self.channel.id = "123" # Default ID
        self.content = ""
        self.created_at = datetime.now()
        self.reference = None
        self.mentions = []
        self.guild = MagicMock() # To simulate non-DM

# Mock the module
mock_discord = MagicMock()
mock_discord.Client = DummyClient
mock_discord.Message = DummyMessage
# Ensure DMChannel has id for safety, though technically DMs have ids
MockDMChannel = type('DMChannel', (), {'id': 'DM_ID'})
mock_discord.DMChannel = MockDMChannel
mock_discord.TextChannel = type('TextChannel', (), {'id': 'TXT_ID'})

sys.modules["discord"] = mock_discord

from auric.interface.adapters.discord import DiscordPact, AuricDiscordClient

class TestDiscordFiltering(unittest.TestCase):
    def setUp(self):
        # We disable whitelist to focus purely on filtering logic (mention/name)
        # If whitelist is empty, it allows all (per implementation).
        self.pact = DiscordPact(token="fake_token", allowed_channels=[], allowed_users=[], agent_name="Auric")
        self.pact.client = AuricDiscordClient(self.pact)
        self.pact._emit = AsyncMock()
        
        # Setup common message attributes
        self.message = MagicMock()
        self.message.author.id = "456" 
        self.message.channel.id = "123"
        self.message.author.name = "User"
        self.message.created_at = datetime.now()
        self.message.mentions = []
        self.message.reference = None
        # Important: Make sure it's NOT a DM by default
        self.message.channel = mock_discord.TextChannel() 

    def test_ignore_irrelevant_message(self):
        """Test that a normal message without mention/name is ignored."""
        self.message.content = "Just chatting about stuff."
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_not_called()

    def test_trigger_on_mention(self):
        """Test that @mention triggers response."""
        self.message.content = "Hey @Bot how are you?"
        # Simulate mention by adding bot user to mentions list
        self.message.mentions = [self.pact.client.user]
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_called_once()
        print("Test Mention: OK")

    def test_trigger_on_name(self):
        """Test that saying 'Auric' triggers response."""
        self.message.content = "Is Auric online?"
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_called_once()
    
    def test_trigger_on_name_case_insensitive(self):
        """Test that saying 'auric' triggers response."""
        self.message.content = "hey auric help me"
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_called_once()

    def test_trigger_on_dm(self):
        """Test that any message in DM triggers response."""
        self.message.content = "Secret message"
        self.message.channel = mock_discord.DMChannel() # Simulate DM
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_called_once()

    def test_trigger_on_reply_cached(self):
        """Test trigger when replying to bot (cached message)."""
        self.message.content = "Replying to you."
        
        # Setup Reference
        ref = MagicMock()
        ref.cached_message.author = self.pact.client.user # Original msg was from Bot
        self.message.reference = ref
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_called_once()

    def test_trigger_on_reply_fetch(self):
        """Test trigger when replying to bot (uncached, need fetch)."""
        self.message.content = "Replying to you."
        
        # Setup Reference logic
        ref = MagicMock()
        ref.cached_message = None
        ref.message_id = "999"
        self.message.reference = ref
        
        # Mock Fetch
        fetched_msg = MagicMock()
        fetched_msg.author = self.pact.client.user
        self.message.channel.fetch_message = AsyncMock(return_value=fetched_msg)
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.message.channel.fetch_message.assert_called_with("999")
        self.pact._emit.assert_called_once()

    def test_ignore_reply_to_others(self):
        """Test confirm we ignore replies to other people."""
        self.message.content = "Replying to someone else."
        
        # Setup Reference
        ref = MagicMock()
        other_user = MagicMock()
        other_user.id = "OTHER_ID"
        ref.cached_message.author = other_user 
        self.message.reference = ref
        
        asyncio.run(self.pact.client.on_message(self.message))
        
        self.pact._emit.assert_not_called()

if __name__ == "__main__":
    unittest.main()
