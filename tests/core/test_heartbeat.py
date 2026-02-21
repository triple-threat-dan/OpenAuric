import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auric.core.heartbeat import HeartbeatManager, can_dream, run_dream_cycle_task, run_heartbeat_task

@pytest.fixture(autouse=True)
def reset_heartbeat_singleton():
    """Resets the singleton instance before each test."""
    HeartbeatManager._instance = None
    yield
    HeartbeatManager._instance = None

@pytest.fixture
def mock_audit_logger():
    logger = MagicMock()
    logger.log_heartbeat = AsyncMock()
    return logger

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.agents.defaults.heartbeat.active_hours = "09:00-17:00"
    return config

def test_heartbeat_manager_singleton():
    hb1 = HeartbeatManager.get_instance()
    hb2 = HeartbeatManager.get_instance()
    assert hb1 is hb2

def test_heartbeat_manager_touch():
    hb = HeartbeatManager()
    old_time = hb.last_active
    # Small sleep to ensure time difference
    import time
    time.sleep(0.001)
    hb.touch()
    assert hb.last_active > old_time

def test_heartbeat_manager_is_idle():
    hb = HeartbeatManager()
    # Mock last active to 40 mins ago
    hb._last_active_timestamp = datetime.now() - timedelta(minutes=40)
    assert hb.is_idle(threshold_minutes=30) is True
    assert hb.is_idle(threshold_minutes=50) is False

@pytest.mark.asyncio
async def test_can_dream_conditions(tmp_path):
    # Setup AURIC_ROOT
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    log_dir = mock_root / "logs"
    log_dir.mkdir()
    log_file = log_dir / "current_session.log"

    with patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        hb = HeartbeatManager.get_instance()
        
        # Case 1: Active user
        hb.touch()
        assert can_dream() is False

        # Case 2: Idle but no log file
        hb._last_active_timestamp = datetime.now() - timedelta(minutes=40)
        assert can_dream() is False

        # Case 3: Idle but empty log file
        log_file.write_text("")
        assert can_dream() is False

        # Case 4: Idle with data
        log_file.write_text("Some logs")
        assert can_dream() is True

@pytest.mark.asyncio
async def test_run_dream_cycle_task_success():
    with patch("auric.core.heartbeat.can_dream", return_value=True), \
         patch("auric.memory.chronicles.perform_dream_cycle", new_callable=AsyncMock) as mock_perform:
        await run_dream_cycle_task()
        mock_perform.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_dream_cycle_task_skipped():
    with patch("auric.core.heartbeat.can_dream", return_value=False), \
         patch("auric.memory.chronicles.perform_dream_cycle", new_callable=AsyncMock) as mock_perform:
        await run_dream_cycle_task()
        mock_perform.assert_not_called()

@pytest.mark.asyncio
async def test_run_dream_cycle_task_failure(caplog):
    with patch("auric.core.heartbeat.can_dream", return_value=True), \
         patch("auric.memory.chronicles.perform_dream_cycle", side_effect=Exception("Nightmare")) as mock_perform:
        await run_dream_cycle_task()
        assert "Nightmare detected" in caplog.text

@pytest.mark.asyncio
async def test_run_heartbeat_task_active_hours(tmp_path, mock_config, mock_audit_logger):
    # Setup AURIC_ROOT and HEARTBEAT.md
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    hb_file = mock_root / "HEARTBEAT.md"
    hb_file.write_text("- Task 1")

    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    
    command_bus = asyncio.Queue()

    # 1. Inside hours
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-23:59"
    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(command_bus)
        
        # Verify Audit Log
        mock_audit_logger.log_heartbeat.assert_awaited_with(
            status="ALIVE",
            meta={
                "active_window": "00:00-23:59",
                "in_hours": True,
                "has_heartbeat_file": True
            }
        )
        
        # Verify Bus message
        msg = await command_bus.get()
        assert msg["source"] == "HEARTBEAT"
        assert "Task 1" in msg["heartbeat_source_content"]

    # 2. Outside hours
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-00:01" # Assuming it's not currently this minute
    # Make sure we are definitely outside
    now = datetime.now()
    if now.hour == 0 and now.minute <= 1:
        mock_config.agents.defaults.heartbeat.active_hours = "23:58-23:59"

    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(command_bus)
        # Should NOT put second message on bus
        assert command_bus.empty()

@pytest.mark.asyncio
async def test_run_heartbeat_midnight_crossing(tmp_path, mock_config, mock_audit_logger):
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    
    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    
    # Mock current time to 01:00
    with patch("auric.core.heartbeat.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.now().replace(hour=1, minute=0)
        mock_dt.min = datetime.min
        mock_dt.combine = datetime.combine
        
        # Scenario: Start 22:00, End 04:00 (Crosses midnight)
        mock_config.agents.defaults.heartbeat.active_hours = "22:00-04:00"
        
        with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
             patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
            await run_heartbeat_task()
            # Status should be ALIVE (01:00 is between 22:00 and 04:00)
            mock_audit_logger.log_heartbeat.assert_awaited()
            # Check the called args
            call_args = mock_audit_logger.log_heartbeat.call_args
            assert call_args.kwargs["status"] == "ALIVE"

        # Scenario: Start 04:00, End 22:00 (Doesn't cross midnight, should skip 01:00)
        mock_audit_logger.log_heartbeat.reset_mock()
        mock_config.agents.defaults.heartbeat.active_hours = "04:00-22:00"
        with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
             patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
            await run_heartbeat_task()
            call_args = mock_audit_logger.log_heartbeat.call_args
            assert call_args.kwargs["status"] == "SKIPPED"

        # Scenario: Start 22:00, End 04:00 (Crosses midnight), current time 12:00 (Outside)
        mock_dt.now.return_value = datetime.now().replace(hour=12, minute=0)
        mock_audit_logger.log_heartbeat.reset_mock()
        mock_config.agents.defaults.heartbeat.active_hours = "22:00-04:00"
        with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
             patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
            await run_heartbeat_task()
            # Status should be SKIPPED (12:00 is NOT between 22:00 and 04:00)
            call_args = mock_audit_logger.log_heartbeat.call_args
            assert call_args.kwargs["status"] == "SKIPPED"

@pytest.mark.asyncio
async def test_run_heartbeat_parsing_error(mock_config, mock_audit_logger, caplog):
    mock_config.agents.defaults.heartbeat.active_hours = "invalid-hours"
    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    
    with patch("auric.core.heartbeat.load_config", return_value=mock_config):
        await run_heartbeat_task()
        assert "Could not parse active hours" in caplog.text
        # Should fail open (status ALIVE)
        mock_audit_logger.log_heartbeat.assert_awaited()
        assert mock_audit_logger.log_heartbeat.call_args.kwargs["status"] == "ALIVE"

@pytest.mark.asyncio
async def test_run_heartbeat_no_pending_tasks(tmp_path, mock_config, mock_audit_logger):
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    hb_file = mock_root / "HEARTBEAT.md"
    hb_file.write_text("No dashes here")

    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-23:59"
    
    command_bus = asyncio.Queue()
    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(command_bus)
        assert command_bus.empty()

@pytest.mark.asyncio
async def test_run_heartbeat_bus_error(tmp_path, mock_config, mock_audit_logger, caplog):
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    hb_file = mock_root / "HEARTBEAT.md"
    hb_file.write_text("- some task")

    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-23:59"
    
    mock_bus = MagicMock()
    mock_bus.put.side_effect = Exception("Queue full or whatever")
    
    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(mock_bus)
        assert "Heartbeat Bus Error: Queue full" in caplog.text

@pytest.mark.asyncio
async def test_run_heartbeat_no_bus(tmp_path, mock_config, mock_audit_logger, caplog):
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    hb_file = mock_root / "HEARTBEAT.md"
    hb_file.write_text("- some task")

    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-23:59"
    
    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(None)
        assert "Heartbeat: No command_bus connection!" in caplog.text

@pytest.mark.asyncio
async def test_run_heartbeat_no_file(tmp_path, mock_config, mock_audit_logger):
    mock_root = tmp_path / ".auric"
    mock_root.mkdir()
    # No HEARTBEAT.md
    
    hb = HeartbeatManager.get_instance()
    hb.audit_logger = mock_audit_logger
    mock_config.agents.defaults.heartbeat.active_hours = "00:00-23:59"
    
    with patch("auric.core.heartbeat.load_config", return_value=mock_config), \
         patch("auric.core.heartbeat.AURIC_ROOT", mock_root):
        await run_heartbeat_task(asyncio.Queue())
        # Should just return without error
        pass
