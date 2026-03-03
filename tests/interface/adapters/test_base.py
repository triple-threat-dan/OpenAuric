import pytest
from datetime import datetime
from auric.interface.adapters.base import PactEvent, BasePact


def test_pact_event_creation():
    """Test standard valid creation of PactEvent"""
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
    """Test full valid creation of PactEvent"""
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


# --- Dummy Concrete Implementation for Testing BasePact ---

class DummyPact(BasePact):
    async def start(self) -> None:
        await super().start()

    async def stop(self) -> None:
        await super().stop()

    async def send_message(self, target_id: str, content: str) -> None:
        await super().send_message(target_id, content)


@pytest.fixture
def dummy_pact():
    return DummyPact()

@pytest.mark.asyncio
async def test_base_pact_abstract_methods(dummy_pact):
    """Test calling the abstract methods (which just have pass)"""
    await dummy_pact.start()
    await dummy_pact.stop()
    await dummy_pact.send_message("123", "hello")

@pytest.mark.asyncio
async def test_base_pact_trigger_typing(dummy_pact):
    """Test default trigger_typing implementation"""
    # Should not raise any errors
    await dummy_pact.trigger_typing("123")


@pytest.mark.asyncio
async def test_base_pact_stop_typing(dummy_pact):
    """Test default stop_typing implementation"""
    # Should not raise any errors
    await dummy_pact.stop_typing("123")


def test_base_pact_get_tools_definition(dummy_pact):
    """Test default get_tools_definition implementation"""
    assert dummy_pact.get_tools_definition() == ""


def test_base_pact_get_tool_names(dummy_pact):
    """Test default get_tool_names implementation"""
    assert dummy_pact.get_tool_names() == []


def test_base_pact_get_tools_schema(dummy_pact):
    """Test default get_tools_schema implementation"""
    assert dummy_pact.get_tools_schema() == []


@pytest.mark.asyncio
async def test_base_pact_execute_tool(dummy_pact):
    """Test execute_tool raises NotImplementedError"""
    with pytest.raises(NotImplementedError, match="Tool test_tool not implemented in DummyPact"):
        await dummy_pact.execute_tool("test_tool", {"arg": "val"})


@pytest.mark.asyncio
async def test_base_pact_on_message_and_emit(dummy_pact):
    """Test message handler registration and emission"""
    
    received_event = None
    
    async def mock_handler(event: PactEvent):
        nonlocal received_event
        received_event = event

    dummy_pact.on_message(mock_handler)
    
    test_event = PactEvent(
        platform="test",
        sender_id="111",
        content="hi"
    )
    
    await dummy_pact._emit(test_event)
    
    assert received_event is not None
    assert received_event == test_event


@pytest.mark.asyncio
async def test_base_pact_emit_without_handler(dummy_pact):
    """Test emission does not crash if no handler is registered"""
    test_event = PactEvent(
        platform="test",
        sender_id="111",
        content="hi"
    )
    # Should do nothing and not raise an error
    await dummy_pact._emit(test_event)
