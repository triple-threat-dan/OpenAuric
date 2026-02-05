import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

# Mock external dependencies to avoid ModuleNotFoundError
mock_telegram = MagicMock()
sys.modules["telegram"] = mock_telegram
sys.modules["telegram.ext"] = mock_telegram
sys.modules["telegram.error"] = mock_telegram

mock_discord = MagicMock()
sys.modules["discord"] = mock_discord

from auric.core.config import AuricConfig
from auric.core.database import AuditLogger, TaskExecution
from auric.interface.pact_manager import PactManager
from auric.interface.adapters.base import PactEvent

async def test_pact_manager_resume_logic():
    # Setup Mocks
    mock_config = MagicMock() # Removed spec=AuricConfig to avoid subtle introspection issues
    mock_config.pacts.telegram.enabled = False
    mock_config.pacts.discord.enabled = False
    
    mock_audit = MagicMock(spec=AuditLogger)
    mock_bus = asyncio.Queue()
    
    manager = PactManager(mock_config, mock_audit, mock_bus)

    # Scenerio 1: Pending Task Exists + Approval Message
    pending_task = TaskExecution(id="task-123", goal="Test", status="PENDING_APPROVAL", started_at=datetime.now())
    mock_audit.get_pending_approval_task = AsyncMock(return_value=pending_task)
    mock_audit.update_status = AsyncMock()

    event = PactEvent(
        platform="telegram",
        sender_id="user1",
        content="Yes, proceed",
        timestamp=datetime.now()
    )

    await manager.handle_message(event)

    # Verify Logic
    mock_audit.update_status.assert_called_with("task-123", "RUNNING")
    
    # Verify Bus Signal
    signal = await mock_bus.get()
    assert signal["type"] == "resume_signal"
    assert signal["task_id"] == "task-123"

async def test_pact_manager_resume_denial():
    # Setup Mocks
    mock_config = MagicMock()
    mock_config.pacts.telegram.enabled = False
    mock_config.pacts.discord.enabled = False
    
    mock_audit = MagicMock(spec=AuditLogger)
    mock_bus = asyncio.Queue()
    
    manager = PactManager(mock_config, mock_audit, mock_bus)

    # Scenerio 2: Pending Task Exists + Denial Message
    pending_task = TaskExecution(id="task-124", goal="Test", status="PENDING_APPROVAL", started_at=datetime.now())
    mock_audit.get_pending_approval_task = AsyncMock(return_value=pending_task)
    mock_audit.update_status = AsyncMock()

    event = PactEvent(
        platform="telegram",
        sender_id="user1",
        content="No, stop it",
        timestamp=datetime.now()
    )

    await manager.handle_message(event)

    # Verify Logic
    mock_audit.update_status.assert_called_with("task-124", "CANCELLED")
    
    # Verify Bus Signal
    signal = await mock_bus.get()
    assert signal["type"] == "cancel_signal"
    assert signal["task_id"] == "task-124"

async def test_pact_manager_new_query():
    # Setup Mocks
    mock_config = MagicMock()
    mock_config.pacts.telegram.enabled = False
    mock_config.pacts.discord.enabled = False
    
    mock_audit = MagicMock(spec=AuditLogger)
    mock_bus = asyncio.Queue()
    
    manager = PactManager(mock_config, mock_audit, mock_bus)

    # Scenerio 3: No Pending Task
    mock_audit.get_pending_approval_task = AsyncMock(return_value=None)
    mock_audit.update_status = AsyncMock()

    event = PactEvent(
        platform="discord",
        sender_id="user2",
        content="Hello, how are you?",
        timestamp=datetime.now()
    )

    await manager.handle_message(event)

    # Verify Logic
    mock_audit.update_status.assert_not_called()
    
    # Verify Bus Signal is just "user_query"
    signal = await mock_bus.get()
    assert signal["type"] == "user_query"
    assert signal["event"] == event

async def main():
    print("Running tests...")
    try:
        await test_pact_manager_resume_logic()
        print("✅ test_pact_manager_resume_logic passed")
        
        await test_pact_manager_resume_denial()
        print("✅ test_pact_manager_resume_denial passed")
        
        await test_pact_manager_new_query()
        print("✅ test_pact_manager_new_query passed")
        
        print("All tests passed!")
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
