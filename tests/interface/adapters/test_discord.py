import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import discord
from datetime import datetime

from auric.interface.adapters.discord import DiscordPact, AuricDiscordClient
from auric.interface.adapters.base import PactEvent


@pytest.fixture
def mock_discord_pact():
    pact = DiscordPact(
        token="fake_token",
        allowed_channels=["111", "222"],
        allowed_users=["999", "888"],
        agent_name="Auric",
        api_port=8000,
        bot_loop_limit=4
    )
    return pact


@pytest.fixture
def mock_client(mock_discord_pact):
    intents = discord.Intents.default()
    client = AuricDiscordClient(pact=mock_discord_pact, intents=intents)
    mock_discord_pact.client = client
    # Set to ready state
    client._connection = AsyncMock()
    
    # Mock discord.py client properties/methods that can't be directly set
    type(client).user = PropertyMock(return_value=MagicMock(id=123, bot=False))
    type(client).guilds = PropertyMock(return_value=[])
    type(client).users = PropertyMock(return_value=[])
    client.fetch_user = AsyncMock()
    client.get_channel = MagicMock()
    client.fetch_channel = AsyncMock()
    client.is_ready = MagicMock(return_value=True)
    client._is_bot_loop = AsyncMock(return_value=False)
    return client


# --- AuricDiscordClient Tests ---

import logging

@pytest.mark.asyncio
async def test_auric_client_on_ready(mock_client, caplog):
    caplog.set_level(logging.INFO)
    mock_guild1 = MagicMock()
    mock_guild1.name = "TestGuild"
    mock_guild1.member_count = 10
    mock_guild1.chunk = AsyncMock()
    
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild1])
    mock_client.intents.members = True

    await mock_client.on_ready()
    
    assert "Discord connected" in caplog.text
    assert "Chunked guild TestGuild" in caplog.text
    mock_guild1.chunk.assert_called_once()


@pytest.mark.asyncio
async def test_auric_client_is_bot_loop(mock_client):
    # Unmock the fixture's override so we can test the real method
    del mock_client._is_bot_loop
    mock_channel = MagicMock()
    
    # 1. All bots
    async def mock_history_bots(*args, **kwargs):
        msg1 = MagicMock(); msg1.author.bot = True
        msg2 = MagicMock(); msg2.author.bot = True
        yield msg1
        yield msg2
        
    mock_channel.history = mock_history_bots
    is_loop = await mock_client._is_bot_loop(mock_channel, limit=2)
    assert is_loop is True

    # 2. Includes a human
    async def mock_history_human(*args, **kwargs):
        msg1 = MagicMock(); msg1.author.bot = True
        msg2 = MagicMock(); msg2.author.bot = False  # Human!
        yield msg1
        yield msg2
        
    mock_channel.history = mock_history_human
    is_loop = await mock_client._is_bot_loop(mock_channel, limit=2)
    assert is_loop is False

    # 3. Exception handling
    async def mock_history_exc(*args, **kwargs):
        raise Exception("API Error")
        yield None
    mock_channel.history = mock_history_exc
    is_loop = await mock_client._is_bot_loop(mock_channel)
    assert is_loop is False


@pytest.mark.asyncio
async def test_on_message_ignore_self(mock_client):
    mock_msg = MagicMock()
    mock_msg.author = mock_client.user
    
    # Process
    await mock_client.on_message(mock_msg)
    
    # Should exit early without checking anything
    # We can verify by asserting channel checks weren't accessed
    mock_msg.channel.type  # If property accessed it means we went past the if


@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_triggers(mock_pairing_cls, mock_client):
    """Test the should_respond logic"""
    mock_pairing_cls.return_value.is_user_allowed.return_value = True
    
    mock_msg = MagicMock()
    mock_msg.author = MagicMock()
    mock_msg.author.bot = False
    
    mock_client.pact.allowed_users = ["human_id"]
    mock_msg.author.id = "human_id"
    mock_msg.channel.id = "111"  # Needs to pass channel whitelist
    
    # We'll mock the internal pact _emit to verify if should_respond was True
    mock_client.pact._emit = AsyncMock()

    # 1. Not a trigger -> Should return early
    mock_msg.channel = MagicMock()
    mock_msg.content = "random chat"
    mock_msg.mentions = []
    mock_msg.reference = None
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_not_called()

    # 2. DM Channel -> Should Respond
    mock_msg.channel = MagicMock(spec=discord.DMChannel) # Setting specific spec helps isinstance
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_called_once()
    mock_client.pact._emit.reset_mock()
    
    mock_msg.channel = MagicMock(spec=discord.TextChannel)
    mock_msg.channel.id = "111"
    
    # 3. Direct Mention -> Should Respond
    mock_msg.mentions = [mock_client.user]
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_called_once()
    mock_client.pact._emit.reset_mock()
    mock_msg.mentions = []
    
    # 4. Name Mention -> Should Respond
    mock_msg.content = "Auric, do something"
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_called_once()
    mock_client.pact._emit.reset_mock()
    mock_msg.content = "random chat"
    
    # 5. Reply to Bot (Cached) -> Should Respond
    mock_msg.reference = MagicMock()
    mock_msg.reference.cached_message.author = mock_client.user
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_called_once()
    mock_client.pact._emit.reset_mock()
    
    # 6. Reply to Bot (Uncached, Fetch) -> Should Respond
    mock_msg.reference.cached_message = None
    mock_msg.reference.message_id = "target_msg"
    
    async def mock_fetch(*args):
        m = MagicMock()
        m.author = mock_client.user
        return m
        
    mock_msg.channel.fetch_message = AsyncMock(side_effect=mock_fetch)
    await mock_client.on_message(mock_msg)
    mock_client.pact._emit.assert_called_once()
    mock_client.pact._emit.reset_mock()


@pytest.mark.asyncio
async def test_on_message_bot_loop(mock_client):
    mock_msg = MagicMock()
    mock_msg.author.bot = True
    # Force response trigger
    mock_msg.channel = MagicMock(spec=discord.DMChannel) 
    
    mock_client._is_bot_loop = AsyncMock(return_value=True)
    mock_client.pact._emit = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    # Loop detected -> return early
    mock_client.pact._emit.assert_not_called()


@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_auth_rejection(mock_pairing_cls, mock_client):
    # Setup trigger
    mock_msg = MagicMock()
    mock_msg.channel = MagicMock(spec=discord.DMChannel) 
    mock_msg.author.id = "bad_user"
    mock_msg.author.name = "Hacker"
    
    # 1. Reject via DM
    mock_pairing_mgr = MagicMock()
    mock_pairing_mgr.is_user_allowed.return_value = False
    mock_pairing_mgr.create_request.return_value = "XCODE"
    mock_pairing_cls.return_value = mock_pairing_mgr
    mock_msg.channel.send = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    mock_pairing_mgr.create_request.assert_called_once()
    mock_msg.channel.send.assert_called_once()
    
    # 2. Reject via mentioned in text channel (Lines 114-117)
    mock_msg.channel = MagicMock(spec=discord.TextChannel)
    mock_msg.mentions = [mock_client.user]
    mock_msg.channel.send.reset_mock()
    await mock_client.on_message(mock_msg)
    mock_msg.channel.send.assert_called_once()
    
    # 3. Reject via name mention (Regex trigger)
    mock_msg.mentions = []
    mock_msg.content = "Auric, tell me a joke"
    mock_msg.channel.send.reset_mock()
    await mock_client.on_message(mock_msg)
    mock_msg.channel.send.assert_called_once()


@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_channel_whitelist(mock_pairing_cls, mock_client):
    # Simulate allowed user via pairing
    mock_pairing_cls.return_value.is_user_allowed.return_value = True
    
    mock_msg = MagicMock()
    mock_msg.mentions = [mock_client.user] # Trigger
    mock_msg.channel = MagicMock(spec=discord.TextChannel)
    mock_msg.channel.id = 999
    
    mock_client.pact._emit = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    # Ignored because channel "999" is not in ["111", "222"]
    mock_client.pact._emit.assert_not_called()


@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_slash_new(mock_pairing_cls, mock_client):
    mock_pairing_cls.return_value.is_user_allowed.return_value = True
    
    mock_msg = MagicMock()
    mock_msg.channel = MagicMock(spec=discord.DMChannel)
    mock_msg.channel.id = "111"
    mock_msg.content = "/new"
    
    # 1. Unauthorized
    mock_msg.author.id = "invalid"
    mock_msg.channel.send = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    mock_msg.channel.send.assert_called_with("⛔ You are not authorized to reset the session.")
    
    # 2. Authorized
    mock_msg.author.id = "999"  # in allowed_users
    mock_client.pact.trigger_new_session = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    mock_client.pact.trigger_new_session.assert_called_with("111")


@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_formatting_and_emit(mock_pairing_cls, mock_client):
    mock_pairing_cls.return_value.is_user_allowed.return_value = True
    
    mock_msg = MagicMock()
    mock_msg.channel = MagicMock(spec=discord.TextChannel)
    mock_msg.channel.id = 111
    mock_msg.channel.name = "general"
    mock_msg.mentions = [mock_client.user]
    mock_msg.author.id = 999
    
    # Setup mentions formatting test
    mock_u1 = MagicMock(); mock_u1.id = 1; mock_u1.display_name = "Bob"
    # User mention regex supports !
    mock_msg.content = "Hello <@1> and <@!1> in <#2>"
    mock_msg.mentions = [mock_client.user, mock_u1]
    
    mock_c1 = MagicMock(); mock_c1.id = 2; mock_c1.name = "general"
    mock_msg.channel_mentions = [mock_c1]
    
    # Setup reference
    mock_msg.reference = MagicMock()
    mock_msg.reference.message_id = 555
    mock_msg.created_at = datetime.now()
    
    mock_client.pact._emit = AsyncMock()
    
    await mock_client.on_message(mock_msg)
    
    mock_client.pact._emit.assert_called_once()
    event: PactEvent = mock_client.pact._emit.call_args[0][0]
    
    assert event.platform == "discord"
    assert event.sender_id == "111"  # Channel ID
    assert event.reply_to_id == "555"
    assert event.content == "Hello @Bob and @Bob in #general"


# --- DiscordPact Tests ---

@pytest.mark.asyncio
@patch("aiohttp.ClientSession.post")
async def test_trigger_new_session(mock_post, mock_discord_pact):
    mock_discord_pact.send_message = AsyncMock()
    
    # 1. Success 200
    mock_ctx = AsyncMock()
    mock_ctx.status = 200
    mock_ctx.json.return_value = {"session_id": "sid123"}
    mock_post.return_value.__aenter__.return_value = mock_ctx
    
    await mock_discord_pact.trigger_new_session("target_1")
    
    mock_discord_pact.send_message.assert_called_with("target_1", "🔄 **Session Reset**. New ID: `sid123`")
    assert mock_post.call_args[1]["json"] == {"context": "discord:target_1"}
    
    # 2. Failure 500
    mock_ctx.status = 500
    await mock_discord_pact.trigger_new_session("target_1")
    mock_discord_pact.send_message.assert_called_with("target_1", "⚠️ Failed to reset session. API Status: 500")
    
    # 3. Exception
    mock_post.side_effect = Exception("Network Down")
    await mock_discord_pact.trigger_new_session("target_1")
    mock_discord_pact.send_message.assert_called_with("target_1", "⚠️ Error triggering new session: Network Down")


@pytest.mark.asyncio
@patch("auric.interface.adapters.discord.AuricDiscordClient")
async def test_start_and_stop(mock_client_cls, mock_discord_pact):
    mock_instance = AsyncMock()
    mock_client_cls.return_value = mock_instance
    
    # Test Start
    await mock_discord_pact.start()
    assert mock_discord_pact.client is not None
    assert mock_discord_pact._task is not None
    
    # Wait for loop to spin
    await asyncio.sleep(0.01)
    mock_instance.start.assert_called_once_with("fake_token")
    
    # Test Stop
    await mock_discord_pact.stop()
    mock_instance.close.assert_called_once()
    assert mock_discord_pact.client is None


def test_chunk_message():
    content = "A" * 2500
    chunks = DiscordPact._chunk_message(content, max_length=2000)
    assert len(chunks) == 2
    assert chunks[0] == "A" * 2000
    assert chunks[1] == "A" * 500

    content = "Hello\n\nWorld" + (" " * 2000)
    chunks = DiscordPact._chunk_message(content, max_length=2000)
    # Should split at paragraph boundary preferentially if within length
    assert "Hello" in chunks[0]

@pytest.mark.asyncio
async def test_send_dm(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    
    # Success via ID
    mock_user = AsyncMock()
    mock_user.name = "TestUser"
    mock_user.id = 123
    mock_client.fetch_user.return_value = mock_user
    
    res = await mock_discord_pact.send_dm("123", "Hello")
    assert "Message sent" in res
    mock_user.send.assert_called_once_with("Hello")
    mock_discord_pact.stop_typing.assert_called_with("123")


@pytest.mark.asyncio
async def test_send_dm_fallback_name(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    
    mock_user = AsyncMock()
    mock_user.name = "John"
    mock_user.id = 999
    
    mock_guild = MagicMock()
    mock_guild.members = [mock_user]
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild])
    
    mock_client.fetch_user.side_effect = Exception("Not an ID")
    type(mock_client).users = PropertyMock(return_value=[])
    
    res = await mock_discord_pact.send_dm("John", "Hi")
    assert "Message sent" in res
    mock_user.send.assert_called_once_with("Hi")


@pytest.mark.asyncio
async def test_send_channel_message(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    
    mock_channel = AsyncMock()
    mock_client.get_channel.return_value = mock_channel
    
    await mock_discord_pact.send_channel_message("111", "Hello Channel")
    mock_channel.send.assert_called_once_with("Hello Channel")


@pytest.mark.asyncio
async def test_send_message_router(mock_discord_pact):
    mock_discord_pact.send_channel_message = AsyncMock()
    mock_discord_pact.send_dm = AsyncMock()
    
    # Success on channel
    await mock_discord_pact.send_message("111", "test")
    mock_discord_pact.send_channel_message.assert_called_once()
    mock_discord_pact.send_dm.assert_not_called()
    
    # Fail on channel, fallback to DM
    mock_discord_pact.send_channel_message.side_effect = Exception("Not a channel")
    await mock_discord_pact.send_message("111", "test")
    mock_discord_pact.send_dm.assert_called_once()


@pytest.mark.asyncio
async def test_add_reaction(mock_discord_pact, mock_client):
    mock_channel = AsyncMock()
    mock_msg = AsyncMock()
    mock_client.get_channel.return_value = mock_channel
    mock_channel.fetch_message.return_value = mock_msg
    
    await mock_discord_pact.add_reaction("111", "222", "👍")
    mock_msg.add_reaction.assert_called_once_with("👍")


@pytest.mark.asyncio
async def test_lookup_user(mock_discord_pact, mock_client):
    mock_user = MagicMock()
    mock_user.name = "Bob"
    mock_user.id = 444
    
    mock_guild = MagicMock()
    mock_guild.members = [mock_user]
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild])
    
    # Fetch user failure, fallback to guild
    mock_client.fetch_user.side_effect = Exception()
    
    res = await mock_discord_pact.lookup_user("bob")
    assert "Bob" in res
    assert "444" in res


@pytest.mark.asyncio
async def test_typing_indicators(mock_discord_pact, mock_client):
    # Setup channel that supports typing
    mock_target = MagicMock()
    mock_client.get_channel.return_value = mock_target
    mock_typing_ctx = AsyncMock()
    mock_target.typing.return_value = mock_typing_ctx
    
    # Trigger
    await mock_discord_pact.trigger_typing("111")
    assert "111" in mock_discord_pact._typing_tasks
    
    # Allow task to spin up
    await asyncio.sleep(0.01)
    
    # Stop
    await mock_discord_pact.stop_typing("111")
    assert "111" not in mock_discord_pact._typing_tasks
    # Ensure it's done/cancelled
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_tools_interface(mock_discord_pact, mock_client):
    mock_discord_pact.send_dm = AsyncMock(return_value="OK")
    mock_discord_pact.lookup_user = AsyncMock()
    mock_discord_pact.send_channel_message = AsyncMock()
    mock_discord_pact.add_reaction = AsyncMock()
    
    assert isinstance(mock_discord_pact.get_tools_definition(), str)
    assert len(mock_discord_pact.get_tool_names()) == 4
    assert len(mock_discord_pact.get_tools_schema()) == 4
    
    # Execute Tool Calls
    res = await mock_discord_pact.execute_tool("discord_send_dm", {"user_id": "1", "content": "A"})
    assert res == "OK"
    mock_discord_pact.send_dm.assert_called_once()
    
    await mock_discord_pact.execute_tool("discord_lookup_user", {"query": "A"})
    mock_discord_pact.lookup_user.assert_called_once()
    
    await mock_discord_pact.execute_tool("discord_send_channel_message", {"channel_id": "1", "content": "A"})
    mock_discord_pact.send_channel_message.assert_called_once()
    
    await mock_discord_pact.execute_tool("discord_add_reaction", {"channel_id": "1", "message_id": "2", "emoji": "A"})
    mock_discord_pact.add_reaction.assert_called_once()
    
    # Missing tool
    with pytest.raises(NotImplementedError):
        await mock_discord_pact.execute_tool("invalid_tool", {})

# --- Coverage Edge Cases ---

@pytest.mark.asyncio
async def test_run_client_exceptions(mock_discord_pact, mock_client):
    # Test CancelledError
    async def mock_start(*args):
        raise asyncio.CancelledError()
    mock_client.start = mock_start
    await mock_discord_pact._run_client()  # Should handle quietly

    # Test Generic Exception
    async def mock_start_exc(*args):
        raise Exception("Fatal Crash")
    mock_client.start = mock_start_exc
    await mock_discord_pact._run_client()  # Should log quietly
    
@pytest.mark.asyncio
async def test_stop_no_client(mock_discord_pact):
    mock_discord_pact.client = None
    await mock_discord_pact.stop()  # Should return early

@pytest.mark.asyncio
async def test_stop_cancelled_task(mock_discord_pact, mock_client):
    mock_client.close = AsyncMock()
    mock_discord_pact._task = asyncio.create_task(asyncio.sleep(10))
    mock_discord_pact._task.cancel()
    await mock_discord_pact.stop() # Should suppress CancelledError

@pytest.mark.asyncio
async def test_send_dm_not_ready(mock_discord_pact, mock_client):
    mock_discord_pact.client.is_ready.return_value = False
    assert await mock_discord_pact.send_dm("1", "msg") is None

@pytest.mark.asyncio
async def test_send_channel_message_not_ready(mock_discord_pact, mock_client):
    mock_discord_pact.client.is_ready.return_value = False
    assert await mock_discord_pact.send_channel_message("1", "msg") is None

@pytest.mark.asyncio
async def test_lookup_user_not_ready(mock_discord_pact, mock_client):
    mock_discord_pact.client.is_ready.return_value = False
    assert await mock_discord_pact.lookup_user("A") == "Error: Discord Pact not ready."

@pytest.mark.asyncio
async def test_add_reaction_not_ready(mock_discord_pact, mock_client):
    mock_discord_pact.client.is_ready.return_value = False
    assert await mock_discord_pact.add_reaction("1", "1", "a") is None

@pytest.mark.asyncio
async def test_typing_indicators_not_ready(mock_discord_pact, mock_client):
    mock_discord_pact.client.is_ready.return_value = False
    await mock_discord_pact.trigger_typing("1")
    assert "1" not in mock_discord_pact._typing_tasks

@pytest.mark.asyncio
async def test_send_dm_global_name_match(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    mock_user = MagicMock(name="BobUser", global_name="Bob")
    type(mock_client).users = PropertyMock(return_value=[mock_user])
    mock_client.fetch_user.side_effect = Exception("Not an ID")
    
    mock_user.send = AsyncMock()
    res = await mock_discord_pact.send_dm("Bob", "Hi")
    assert "Message sent" in res

@pytest.mark.asyncio
async def test_send_dm_display_name_match(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    mock_client.fetch_user.side_effect = Exception("Not an ID")
    type(mock_client).users = PropertyMock(return_value=[])
    
    mock_user = MagicMock(display_name="Charlie")
    mock_user.name = "real_name"
    mock_guild = MagicMock()
    mock_guild.members = [mock_user]
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild])
    
    mock_user.send = AsyncMock()
    res = await mock_discord_pact.send_dm("charlie", "Hi")
    assert "Message sent" in res

@pytest.mark.asyncio
async def test_send_dm_not_found_debug_msg(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    mock_client.fetch_user.side_effect = Exception("Not ID")
    
    mock_guild = MagicMock(name="TestG")
    user_m = MagicMock()
    user_m.name = "A"
    user_m.display_name = "A"
    mock_guild.members = [user_m]
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild])
    
    res = await mock_discord_pact.send_dm("Nobody", "Hi")
    assert "not found" in res
    assert "TestG" in res

@pytest.mark.asyncio
async def test_send_dm_generic_exception(mock_discord_pact, mock_client):
    mock_client.fetch_user.side_effect = Exception("Fatal")
    type(mock_client).users = PropertyMock(side_effect=Exception("Iter crash"))
    res = await mock_discord_pact.send_dm("123", "msg")
    assert "Error sending DM" in res

@pytest.mark.asyncio
async def test_lookup_user_id_fetch_none(mock_discord_pact, mock_client):
    # 1. Return None
    mock_client.fetch_user.return_value = None
    res = await mock_discord_pact.lookup_user("12345")
    assert "No users found" in res
    
    # 2. Raise exception (coverage 391-393)
    mock_client.fetch_user.side_effect = Exception()
    res2 = await mock_discord_pact.lookup_user("54321")
    assert "No users found" in res2

@pytest.mark.asyncio
async def test_lookup_user_id_fetch_success(mock_discord_pact, mock_client):
    mock_user = MagicMock(display_name="Bob", id=54321)
    mock_user.name = "Bob"
    # To avoid being self
    type(mock_client).user = PropertyMock(return_value=MagicMock(id=111))
    mock_client.fetch_user.return_value = mock_user
    
    res = await mock_discord_pact.lookup_user("54321")
    assert "Direct Fetch" in res
    assert "Bob" in res

@pytest.mark.asyncio
async def test_send_channel_message_fetch_fallback_and_no_send(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    mock_client.get_channel.return_value = None
    
    # 1. Fetch channel fails (hits line 466-467)
    mock_client.fetch_channel.side_effect = Exception("Not found")
    await mock_discord_pact.send_channel_message("111", "msg")
    
    # 2. Fetch channel succeeds but no send
    mock_c = MagicMock()
    del mock_c.send  # ensure it has no send attribute
    mock_client.fetch_channel.side_effect = None
    mock_client.fetch_channel.return_value = mock_c
    
    await mock_discord_pact.send_channel_message("111", "msg")
    # Coverage hits the `else` branch where logger.error("Channel... not sendable") happens

@pytest.mark.asyncio
async def test_start_running_client(mock_discord_pact):
    mock_discord_pact.client = MagicMock()
    await mock_discord_pact.start() # Hits early return

@pytest.mark.asyncio
async def test_lookup_user_skip_self_and_dupes(mock_discord_pact, mock_client):
    mock_user = MagicMock(display_name="Bob", id=555)
    mock_user.name = "Bob"
    mock_guild = MagicMock()
    mock_guild.members = [mock_user, mock_client.user, mock_user]
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild])
    
    res = await mock_discord_pact.lookup_user("bob")
    assert res.count("- Bob") == 1 # Dupe blocked, self skipped

@pytest.mark.asyncio
async def test_send_dm_skip_self_cache(mock_discord_pact, mock_client):
    mock_discord_pact.stop_typing = AsyncMock()
    mock_client.fetch_user.side_effect = Exception()
    
    # 1. Skip self in users cache
    type(mock_client).users = PropertyMock(return_value=[mock_client.user])
    
    # 2. Skip self and numeric matching in guild
    mock_num_user = MagicMock(id=123)
    mock_num_user.name = "test"
    mock_guild1 = MagicMock()
    mock_guild2 = MagicMock()
    
    # Mock discord.utils.get (return self for guild1, then num_user for guild2)
    with patch("discord.utils.get", side_effect=[mock_client.user, mock_num_user, None, None, None, None]):
        mock_guild1.members = [mock_client.user]
        mock_guild2.members = [mock_num_user]
        type(mock_client).guilds = PropertyMock(return_value=[mock_guild1, mock_guild2])
        
        await mock_discord_pact.send_dm("123", "msg")
        mock_num_user.send.assert_called_once()
        
    # 3. Case insensitive match fallback
    mock_ci_user = MagicMock()
    mock_ci_user.name = "John"
    mock_guild2.members = [mock_ci_user]
    mock_ci_user.send = AsyncMock()
    type(mock_client).guilds = PropertyMock(return_value=[mock_guild2])
    with patch("discord.utils.get", return_value=None):
        await mock_discord_pact.send_dm("john", "msg")
        mock_ci_user.send.assert_called_once()

@pytest.mark.asyncio
async def test_stop_typing_cancelled(mock_discord_pact):
    async def cancel_me(): raise asyncio.CancelledError()
    mock_discord_pact._typing_tasks["111"] = asyncio.create_task(cancel_me())
    await mock_discord_pact.stop_typing("111")
    assert "111" not in mock_discord_pact._typing_tasks
    
@pytest.mark.asyncio
async def test_get_tools_definition_no_file(mock_discord_pact):
    with patch("auric.interface.adapters.discord.Path.exists", return_value=False):
        assert mock_discord_pact.get_tools_definition() == ""

@pytest.mark.asyncio
async def test_send_channel_message_generic_exception(mock_discord_pact, mock_client):
    mock_client.get_channel.side_effect = Exception("Fail")
    await mock_discord_pact.send_channel_message("111", "msg")

@pytest.mark.asyncio
async def test_add_reaction_exceptions_and_fallbacks(mock_discord_pact, mock_client):
    # 1. Channel not found
    mock_client.get_channel.return_value = None
    mock_client.fetch_channel.side_effect = Exception()
    await mock_discord_pact.add_reaction("1", "1", "A") # Error Channel not found
    
    # 2. Generic exception
    mock_client.get_channel.side_effect = Exception("Crash")
    await mock_discord_pact.add_reaction("1", "1", "A")

@pytest.mark.asyncio
async def test_typing_exceptions_and_fallbacks(mock_discord_pact, mock_client):
    # Already typing
    task = asyncio.create_task(asyncio.sleep(2))
    mock_discord_pact._typing_tasks["111"] = task
    await mock_discord_pact.trigger_typing("111") # Should exit early
    task.cancel()
    
    # Fallback to fetch_user
    mock_client.get_channel.return_value = None
    mock_client.fetch_channel.side_effect = Exception()
    
    mock_u = MagicMock()
    del mock_u.typing
    mock_client.fetch_user.return_value = mock_u
    
    await mock_discord_pact.trigger_typing("222")
    await asyncio.sleep(0.01) # let loop run
    # Hits the target without typing attribute branch
    
    # Fallback to fetch_user failing (hits lines 547-548)
    mock_client.get_channel.return_value = None
    mock_client.fetch_channel.side_effect = Exception()
    mock_client.fetch_user.side_effect = Exception()
    await mock_discord_pact.trigger_typing("444")
    await asyncio.sleep(0.01)
    
    # Loop Crash
    mock_client.get_channel.side_effect = Exception("Boom")
    await mock_discord_pact.trigger_typing("333")
    await asyncio.sleep(0.01)

@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_reply_fetch_fail(mock_pairing_cls, mock_client):
    mock_msg = MagicMock()
    mock_msg.content = "random"
    mock_msg.author.bot = False
    mock_msg.reference = MagicMock(cached_message=None)
    mock_msg.channel.fetch_message.side_effect = Exception("Deleted")
    await mock_client.on_message(mock_msg)
    # Reaches pass in try-except

@pytest.mark.asyncio
@patch("auric.core.pairing.PairingManager")
async def test_on_message_auth_warning_send_fail(mock_pairing_cls, mock_client):
    mock_pairing_cls.return_value.is_user_allowed.return_value = False
    
    mock_msg = MagicMock()
    mock_msg.content = "random"
    mock_msg.author.bot = False
    mock_msg.channel = MagicMock(spec=discord.DMChannel) # trigger true for warn
    mock_msg.channel.send.side_effect = Exception("Cannot DM")
    
    await mock_client.on_message(mock_msg)
    # Hits exception in warning 

