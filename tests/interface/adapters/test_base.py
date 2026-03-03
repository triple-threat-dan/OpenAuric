from datetime import datetime

import pytest

from auric.interface.adapters.base import BasePact, PactEvent

def test_pact_event_creation():
    """Test standard valid creation of PactEvent."""
    event = PactEvent(
        platform="discord",
        sender_id="123",
        content="hello"
    )
    assert event.platform == "discord"
    assert event.sender_id == "123"
    assert event.content == "hello"
    assert event.reply_to_id is None
    assert isinstance(event.timestamp, datetime)
    assert event.metadata == {}

def test_pact_event_full_creation():
    """Test full valid creation of PactEvent."""
    timestamp = datetime.now()
    event = PactEvent(
        platform="telegram",
        sender_id="456",
        content="test",
        reply_to_id="789",
        timestamp=timestamp,
        metadata={"user_name": "bob"}
    )
    assert event.platform == "telegram"
    assert event.sender_id == "456"
    assert event.content == "test"
    assert event.reply_to_id == "789"
    assert event.timestamp == timestamp
    assert event.metadata == {"user_name": "bob"}

# --- Dummy Implementation for testing BasePact ---

class DummyPact(BasePact):
    """Concrete implementation of BasePact for testing purposes."""
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_message(self, target_id: str, content: str) -> None:
        pass

@pytest.fixture
def dummy_pact():
    return DummyPact()

@pytest.mark.asyncio
async def test_base_pact_abstract_methods(dummy_pact):
    """Verify that calling implemented abstract methods works."""
    await dummy_pact.start()
    await dummy_pact.stop()
    await dummy_pact.send_message("123", "hello")

@pytest.mark.asyncio
async def test_base_pact_typing_indicators(dummy_pact):
    """Test default typing indicator implementations (no-ops)."""
    await dummy_pact.trigger_typing("123")
    await dummy_pact.stop_typing("123")

def test_base_pact_default_tool_methods(dummy_pact):
    """Test default implementations of tool-related methods."""
    assert dummy_pact.get_tools_definition() == ""
    assert dummy_pact.get_tool_names() == []
    assert dummy_pact.get_tools_schema() == []

@pytest.mark.asyncio
async def test_base_pact_execute_tool_not_implemented(dummy_pact):
    """Verify that execute_tool raises NotImplementedError by default."""
    with pytest.raises(NotImplementedError, match="Tool test_tool not implemented in DummyPact"):
        await dummy_pact.execute_tool("test_tool", {"arg": "val"})

@pytest.mark.asyncio
async def test_base_pact_message_handling(dummy_pact):
    """Test registration and emission of message events."""
    received_event = None
    
    async def handler(event: PactEvent):
        nonlocal received_event
        received_event = event

    dummy_pact.on_message(handler)
    test_event = PactEvent(platform="test", sender_id="111", content="hi")
    
    await dummy_pact._emit(test_event)
    assert received_event == test_event

@pytest.mark.asyncio
async def test_base_pact_emit_no_handler(dummy_pact):
    """Verify that emitting without a registered handler does not raise errors."""
    test_event = PactEvent(platform="test", sender_id="111", content="hi")
    await dummy_pact._emit(test_event)
