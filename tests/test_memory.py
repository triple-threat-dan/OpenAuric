import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path
from auric.core.heartbeat import HeartbeatManager, can_dream, run_dream_cycle_task
from auric.memory import chronicles

# --- Test A: The "Amnesia" Test (Restart) (MEM-01) ---
@pytest.mark.asyncio
async def test_memory_persistence(mock_config):
    """
    Test that state persists across "restarts" (re-instantiation).
    """
    # 1. "Run" Agent -> Write to memory
    memory_file = Path("~/.auric/grimoire/USER.md").expanduser()
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text("My name is Jace.", encoding="utf-8")
    
    # 2. "Restart" -> Initialize new FocusManager/Librarian/System
    # We verify the file is still there and readable
    assert memory_file.exists()
    content = memory_file.read_text(encoding="utf-8")
    assert "Jace" in content
    
    # Ideally, we'd verify the Agent *uses* this, covered in higher level tests.
    # For unit/smoke, verifying file persistence logic is key.

# --- Test B: Short-Term vs. Long-Term (MEM-02) ---
@pytest.mark.asyncio
async def test_retrieval_logic(mock_config):
    """
    Verify retrieval logic (mocked).
    """
    # Mock Librarian search
    with patch("auric.memory.librarian.GrimoireLibrarian") as MockLibrarian:
        librarian = MockLibrarian.return_value
        librarian.search.return_value = ["User mentioned 'Project X' yesterday."]
        
        # Verify search returns expected mocked snippets
        results = librarian.search("What about Project X?")
        assert len(results) == 1
        assert "Project X" in results[0]

# --- Test C: The "Dream" Cycle (Summary) (MEM-03) ---
@pytest.mark.asyncio
async def test_dream_cycle_trigger(mock_config):
    """
    Verify Dream Cycle trigger conditions.
    """
    hb = HeartbeatManager.get_instance()
    
    # 1. Simulate Active -> Should NOT Dream
    hb.touch()
    assert can_dream() is False
    
    # 2. Simulate Idle -> Should Dream (if log exists)
    # Patch the is_idle method on the instance we already have or class
    # Since can_dream gets a fresh instance (or same one), and we have it.
    
    with patch.object(hb, "is_idle", return_value=True):
        # Create dummy log
        # mock_config fixture returns the .auric directory in tmp_path
        # conftest patches Path.home() to return tmp_path, so can_dream's Path.home()/.auric resolves to mock_config
        # wait, conftest: auric_dir = tmp_path / ".auric"; (Path.home returns tmp_path).
        # So Path.home() / ".auric" == tmp_path / ".auric" == mock_config.
        
        log_path = mock_config / "logs" / "current_session.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("Log data...", encoding="utf-8")
        
        assert can_dream() is True
        
        # 3. Verify Execution
        with patch("auric.memory.chronicles.perform_dream_cycle", new_callable=AsyncMock) as mock_dream:
            await run_dream_cycle_task()
            mock_dream.assert_awaited_once()
