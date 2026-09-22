import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from auric.interface.pact_manager import PactManager
from auric.core.config import AuricConfig
from auric.interface.adapters.base import PactEvent


@pytest.fixture
def mock_config():
    config = AuricConfig()
    config.pacts.discord.enabled = False
    config.pacts.telegram.enabled = False
    return config


@pytest.fixture
def mock_audit():
    audit = AsyncMock()
    return audit


@pytest.fixture
def manager(mock_config, mock_audit):
    return PactManager(
        config=mock_config,
        audit_logger=mock_audit,
        command_bus=AsyncMock(),
        internal_bus=AsyncMock()
    )


# --- Test start() ---


@pytest.mark.asyncio
@patch("auric.interface.pact_manager.TelegramPact")
async def test_start_telegram_enabled(mock_telegram_class, mock_config, mock_audit):
    mock_config.pacts.telegram.enabled = True
    mock_config.pacts.telegram.token = "test_telegram_token"
    
    manager = PactManager(mock_config, mock_audit, AsyncMock(), AsyncMock())
    
    # Mix of Async and Sync methods
    mock_telegram_instance = MagicMock()
    mock_telegram_instance.start = AsyncMock()
    mock_telegram_instance.get_tool_names.return_value = ["tg_tool"]
    mock_telegram_instance.get_tools_schema.return_value = [{"name": "tg_tool"}]
    mock_telegram_instance.get_tools_definition.return_value = "tg def"
    
    mock_telegram_class.return_value = mock_telegram_instance
    
    await manager.start()
    
    assert "telegram" in manager.adapters
    mock_telegram_class.assert_called_once_with(token="test_telegram_token")
    mock_telegram_instance.on_message.assert_called_once_with(manager.handle_message)
    mock_telegram_instance.start.assert_called_once()
    assert manager.get_tool_names() == {"tg_tool"}


@pytest.mark.asyncio
@patch("auric.interface.pact_manager.DiscordPact")
async def test_start_discord_enabled(mock_discord_class, mock_config, mock_audit):
    mock_config.pacts.discord.enabled = True
    mock_config.pacts.discord.token = "test_discord_token"
    mock_config.pacts.discord.allowed_channels = ["123"]
    mock_config.pacts.discord.allowed_users = ["456"]
    mock_config.agents.name = "TestAgent"
    mock_config.gateway.port = 8000
    mock_config.pacts.discord.bot_loop_limit = 5
    
    manager = PactManager(mock_config, mock_audit, AsyncMock(), AsyncMock())
    
    mock_discord_instance = MagicMock()
    mock_discord_instance.start = AsyncMock()
    mock_discord_instance.get_tool_names.return_value = ["dc_tool"]
    mock_discord_instance.get_tools_schema.return_value = [{"name": "dc_tool"}]
    mock_discord_instance.get_tools_definition.return_value = "dc def"
    
    mock_discord_class.return_value = mock_discord_instance
    
    await manager.start()
    
    assert "discord" in manager.adapters
    mock_discord_class.assert_called_once_with(
        token="test_discord_token",
        allowed_channels=["123"],
        allowed_users=["456"],
        agent_name="TestAgent",
        api_port=8000,
        bot_loop_limit=5
    )
    mock_discord_instance.on_message.assert_called_once_with(manager.handle_message)
    mock_discord_instance.start.assert_called_once()
    assert manager.get_tool_names() == {"dc_tool"}


@pytest.mark.asyncio
async def test_start_no_pacts(manager, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        await manager.start()
        
    assert not manager.adapters
    assert "No Pacts enabled. Agent is lonely." in caplog.text


# --- Test stop() ---


@pytest.mark.asyncio
async def test_stop(manager):
    mock_adapter1 = MagicMock()
    mock_adapter1.stop = AsyncMock()
    mock_adapter2 = MagicMock()
    mock_adapter2.stop = AsyncMock()
    
    manager.adapters["telegram"] = mock_adapter1
    manager.adapters["discord"] = mock_adapter2
    
    await manager.stop()
    
    mock_adapter1.stop.assert_called_once()
    mock_adapter2.stop.assert_called_once()
    assert not manager.adapters


# --- Test handle_message() ---


@pytest.mark.asyncio
async def test_handle_message_no_pending_task(manager):
    manager.audit.get_pending_approval_task.return_value = None
    
    event = PactEvent(platform="telegram", sender_id="user1", content="hello")
    await manager.handle_message(event)
    
    manager.command_bus.put.assert_called_once()
    call_args = manager.command_bus.put.call_args[0][0]
    assert call_args["type"] == "user_query"
    assert call_args["event"] == event


@pytest.mark.asyncio
async def test_handle_message_approval_with_adapter(manager):
    mock_task = MagicMock()
    mock_task.id = "task_123"
    manager.audit.get_pending_approval_task.return_value = mock_task
    
    mock_adapter = MagicMock()
    mock_adapter.send_message = AsyncMock()
    manager.adapters["discord"] = mock_adapter
    
    event = PactEvent(platform="discord", sender_id="user1", content="yes, go ahead")
    await manager.handle_message(event)
    
    manager.audit.update_status.assert_called_once_with("task_123", "RUNNING")
    manager.command_bus.put.assert_called_once()
    call_args = manager.command_bus.put.call_args[0][0]
    assert call_args["type"] == "resume_signal"
    assert call_args["task_id"] == "task_123"
    assert call_args["platform"] == "discord"
    assert call_args["user_input"] == "yes, go ahead"
    
    mock_adapter.send_message.assert_called_once_with("user1", "✅ Deployment approved. Resuming...")


@pytest.mark.asyncio
async def test_handle_message_approval_no_adapter(manager):
    mock_task = MagicMock()
    mock_task.id = "task_123"
    manager.audit.get_pending_approval_task.return_value = mock_task
    
    # Intentionally empty adapters
    
    event = PactEvent(platform="telegram", sender_id="user1", content="approve")
    await manager.handle_message(event)
    
    manager.audit.update_status.assert_called_once_with("task_123", "RUNNING")


@pytest.mark.asyncio
async def test_handle_message_denial_with_adapter(manager):
    mock_task = MagicMock()
    mock_task.id = "task_123"
    manager.audit.get_pending_approval_task.return_value = mock_task
    
    mock_adapter = MagicMock()
    mock_adapter.send_message = AsyncMock()
    manager.adapters["telegram"] = mock_adapter
    
    event = PactEvent(platform="telegram", sender_id="user1", content="stop that right now")
    await manager.handle_message(event)
    
    manager.audit.update_status.assert_called_once_with("task_123", "CANCELLED")
    manager.command_bus.put.assert_called_once()
    call_args = manager.command_bus.put.call_args[0][0]
    assert call_args["type"] == "cancel_signal"
    assert call_args["task_id"] == "task_123"
    
    mock_adapter.send_message.assert_called_once_with("user1", "🛑 Task cancelled.")


@pytest.mark.asyncio
async def test_handle_message_denial_no_adapter(manager):
    mock_task = MagicMock()
    mock_task.id = "task_123"
    manager.audit.get_pending_approval_task.return_value = mock_task
    
    event = PactEvent(platform="discord", sender_id="user1", content="no")
    await manager.handle_message(event)
    
    manager.audit.update_status.assert_called_once_with("task_123", "CANCELLED")


@pytest.mark.asyncio
async def test_handle_message_pending_task_not_approval_or_denial(manager):
    mock_task = MagicMock()
    mock_task.id = "task_123"
    manager.audit.get_pending_approval_task.return_value = mock_task
    
    event = PactEvent(platform="discord", sender_id="user1", content="what is the task about?")
    await manager.handle_message(event)
    
    # Should fall through to Standard User Query
    manager.audit.update_status.assert_not_called()
    manager.command_bus.put.assert_called_once()
    call_args = manager.command_bus.put.call_args[0][0]
    assert call_args["type"] == "user_query"
    assert call_args["event"] == event


# --- Test Tools Definitions / Schemas / Names ---

@pytest.mark.asyncio
async def test_get_all_tools_definitions(manager):
    mock_adapter1 = MagicMock()
    mock_adapter1.get_tools_definition.return_value = "Tool A definition"
    mock_adapter1.get_tool_names.return_value = ["a"]
    mock_adapter1.get_tools_schema.return_value = []
    mock_adapter1.start = AsyncMock()
    
    mock_adapter2 = MagicMock()
    mock_adapter2.get_tools_definition.return_value = "Tool B definition"
    mock_adapter2.get_tool_names.return_value = ["b"]
    mock_adapter2.get_tools_schema.return_value = []
    mock_adapter2.start = AsyncMock()
    
    manager.adapters["a"] = mock_adapter1
    manager.adapters["b"] = mock_adapter2
    
    defs = manager.get_all_tools_definitions()
    assert "Tool A definition" in defs
    assert "Tool B definition" in defs


@pytest.mark.asyncio
async def test_get_tools_schema(manager):
    mock_adapter1 = MagicMock()
    mock_adapter1.get_tools_schema.return_value = [{"name": "tool_a"}]
    mock_adapter1.get_tool_names.return_value = ["tool_a"]
    mock_adapter1.start = AsyncMock()
    
    manager.adapters["a"] = mock_adapter1
    await manager.start()
    
    schemas = manager.get_tools_schema()
    assert len(schemas) == 1
    assert {"name": "tool_a"} in schemas


@pytest.mark.asyncio
async def test_get_tool_names(manager):
    mock_adapter1 = MagicMock()
    mock_adapter1.get_tool_names.return_value = ["tool_a", "tool_b"]
    mock_adapter1.get_tools_schema.return_value = []
    mock_adapter1.start = AsyncMock()
    
    manager.adapters["a"] = mock_adapter1
    await manager.start()
    
    names = manager.get_tool_names()
    assert names == {"tool_a", "tool_b"}


# --- Test execute_tool() ---

@pytest.mark.asyncio
async def test_execute_tool_no_adapters(manager):
    with pytest.raises(ValueError, match="Tool some_tool not found in any active pact."):
        await manager.execute_tool("some_tool", {})


@pytest.mark.asyncio
async def test_execute_tool_success_no_injection(manager):
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["find_user"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(return_value="User found")
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    result = await manager.execute_tool("find_user", {"user_id": "123"})
    assert result == "User found"
    mock_adapter.execute_tool.assert_called_once_with("find_user", {"user_id": "123"})


@pytest.mark.asyncio
async def test_execute_tool_fails_exception(manager):
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["fail_tool"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(side_effect=RuntimeError("Tool failed"))
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    with pytest.raises(RuntimeError, match="Tool failed"):
        await manager.execute_tool("fail_tool", {})


@pytest.mark.asyncio
async def test_execute_tool_intercept_discord_dm(manager):
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "session_456"
    manager.session_router = mock_router
    
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["discord_send_dm"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(return_value="Message sent")
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    result = await manager.execute_tool("discord_send_dm", {"user_id": "999", "content": "Hello there"})
    
    assert result == "Message sent"
    
    # Verify the injection
    mock_router.get_active_session_id.assert_called_once_with("discord:999")
    manager.audit.log_chat.assert_called_once_with(role="AGENT", content="Hello there", session_id="session_456")


@pytest.mark.asyncio
async def test_execute_tool_intercept_discord_channel(manager):
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = "session_789"
    manager.session_router = mock_router
    
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["discord_send_channel_message"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(return_value="Message sent")
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    result = await manager.execute_tool("discord_send_channel_message", {"channel_id": "channel_01", "content": "Hello channel"})
    
    assert result == "Message sent"
    
    # Verify the injection
    mock_router.get_active_session_id.assert_called_once_with("discord:channel_01")
    manager.audit.log_chat.assert_called_once_with(role="AGENT", content="Hello channel", session_id="session_789")


@pytest.mark.asyncio
async def test_execute_tool_intercept_no_session(manager):
    mock_router = MagicMock()
    mock_router.get_active_session_id.return_value = None # No active session
    manager.session_router = mock_router
    
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["discord_send_dm"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(return_value="Message sent")
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    result = await manager.execute_tool("discord_send_dm", {"user_id": "999", "content": "Hello"})
    
    assert result == "Message sent"
    mock_router.get_active_session_id.assert_called_once_with("discord:999")
    manager.audit.log_chat.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_no_content_or_no_target_id(manager):
    mock_router = MagicMock()
    manager.session_router = mock_router
    
    mock_adapter = MagicMock()
    mock_adapter.get_tool_names.return_value = ["discord_send_dm"]
    mock_adapter.get_tools_schema.return_value = []
    mock_adapter.execute_tool = AsyncMock(return_value="Message sent")
    mock_adapter.start = AsyncMock()
    
    manager.adapters["discord"] = mock_adapter
    await manager.start()
    
    # Missing content
    await manager.execute_tool("discord_send_dm", {"user_id": "999"})
    manager.audit.log_chat.assert_not_called()
    
    # Missing target_id
    await manager.execute_tool("discord_send_dm", {"content": "Hello"})
    manager.audit.log_chat.assert_not_called()


# --- Test trigger_typing / stop_typing ---


@pytest.mark.asyncio
async def test_trigger_typing(manager):
    mock_adapter = MagicMock()
    mock_adapter.trigger_typing = AsyncMock()
    manager.adapters["telegram"] = mock_adapter
    
    await manager.trigger_typing("telegram", "user1")
    mock_adapter.trigger_typing.assert_called_once_with("user1")
    
    # Missing adapter should not error
    await manager.trigger_typing("discord", "user1")


@pytest.mark.asyncio
async def test_stop_typing(manager):
    mock_adapter = MagicMock()
    mock_adapter.stop_typing = AsyncMock()
    manager.adapters["discord"] = mock_adapter
    
    await manager.stop_typing("discord", "user1")
    mock_adapter.stop_typing.assert_called_once_with("user1")
    
    # Missing adapter should not error
    await manager.stop_typing("telegram", "user1")


# --- Test HEURISTICS ---

def test_is_approval(manager):
    assert manager._is_approval("yes")
    assert manager._is_approval("  YES   ")
    assert manager._is_approval("Proceed with deployment.")
    assert manager._is_approval("ok then")
    assert not manager._is_approval("no")
    assert not manager._is_approval("stop processing")


def test_is_denial(manager):
    assert manager._is_denial("no")
    assert manager._is_denial("  NO   ")
    assert manager._is_denial("Cancel the deployment.")
    assert manager._is_denial("stop")
    assert not manager._is_denial("yes")
    assert not manager._is_denial("proceed ahead")
