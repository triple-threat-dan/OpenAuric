import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from auric.interface.pact_manager import PactManager, PactEvent
from auric.core.database import AuditLogger

# --- Fixtures ---
@pytest.fixture
def mock_pact_deps():
    config = MagicMock()
    config.pacts.telegram.enabled = False
    config.pacts.discord.enabled = False
    
    audit = MagicMock(spec=AuditLogger)
    audit.get_pending_approval_task = AsyncMock(return_value=None)
    
    bus = asyncio.Queue()
    return config, audit, bus

# --- Test A: Omnipresence (INT-01) ---
@pytest.mark.asyncio
async def test_omnipresence_shared_state(mock_pact_deps):
    """
    Verify that messages from different sources feed into the same bus.
    """
    config, audit, bus = mock_pact_deps
    manager = PactManager(config, audit, bus)
    
    # 1. Simulate Discord Message
    event1 = PactEvent(platform="discord", content="Remind me to buy milk.", sender_id="user1")
    await manager.handle_message(event1)
    
    # 2. Simulate Telegram Message
    event2 = PactEvent(platform="telegram", content="What did I ask?", sender_id="user1")
    await manager.handle_message(event2)
    
    # 3. Verify Bus received both in order
    assert bus.qsize() == 2
    
    item1 = await bus.get()
    assert item1["type"] == "user_query"
    assert item1["event"].platform == "discord"
    
    item2 = await bus.get()
    assert item2["type"] == "user_query"
    assert item2["event"].platform == "telegram"

# --- Test B: Discord Message Normalization (INT-02) ---
@pytest.mark.asyncio
async def test_discord_normalization(mock_pact_deps):
    """
    Verify Discord payload is normalized to generic Event.
    """
    config, audit, bus = mock_pact_deps
    manager = PactManager(config, audit, bus)
    
    # Raw Discord-like content (simulated via Adapter logic, but here we test the Manager's handling of the Event)
    # The Adapter does the normalization before calling handle_message.
    # So we verify that handle_message treats it correctly.
    
    event = PactEvent(platform="discord", content="<@123> Hello!", sender_id="user_discord_1")
    await manager.handle_message(event)
    
    item = await bus.get()
    assert item["event"].content == "<@123> Hello!"
    assert item["event"].platform == "discord"

# --- Test C: Telegram Image Handling (Stub) (INT-03) ---
@pytest.mark.asyncio
async def test_telegram_image_handling_stub(mock_pact_deps):
    """
    Verify image event handling logic (v0.1 stub).
    """
    config, audit, bus = mock_pact_deps
    manager = PactManager(config, audit, bus)
    
    # Simulate an event with image data (if supported by PactEvent model)
    # Assuming PactEvent has an optional 'attachments' or we just send text about it.
    # If not supported in v0.1 model, we just ensure it doesn't crash on standard text.
    
    event = PactEvent(platform="telegram", content="[Image sent]", sender_id="user_tg_1")
    # In a real impl, we might have `attachments` list.
    
    try:
        await manager.handle_message(event)
    except Exception as e:
        pytest.fail(f"Handling caused exception: {e}")
    
    assert bus.qsize() == 1
