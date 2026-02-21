import json
import logging
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from auric.core.system_logger import SystemLogger, JSONLFormatter
from auric.core.config import AuricConfig


@pytest.fixture(autouse=True)
def reset_singleton():
    SystemLogger._instance = None
    yield
    SystemLogger._instance = None


@pytest.fixture
def mock_config():
    config = AuricConfig()
    config.agents.defaults.logging.enabled = True
    config.agents.defaults.logging.log_dir = ".auric/logs"
    config.agents.defaults.logging.max_size_mb = 10
    config.agents.defaults.logging.backup_count = 5
    return config


def test_system_logger_singleton(mock_config):
    with patch("auric.core.system_logger.Path.mkdir"):
        sl1 = SystemLogger.get_instance(mock_config)
        sl2 = SystemLogger.get_instance(mock_config)
        assert sl1 is sl2


def test_system_logger_initialization_disabled(mock_config):
    mock_config.agents.defaults.logging.enabled = False
    sl = SystemLogger(mock_config)
    assert any(isinstance(h, logging.NullHandler) for h in sl.logger.handlers)


def test_system_logger_initialization_enabled(mock_config, tmp_path):
    mock_config.agents.defaults.logging.log_dir = str(tmp_path / "logs")
    sl = SystemLogger(mock_config)
    
    assert (tmp_path / "logs").exists()
    assert any(isinstance(h, logging.FileHandler) for h in sl.logger.handlers)
    # Check rotation setup
    handler = next(h for h in sl.logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5


def test_system_logger_get_instance_no_config(tmp_path):
    # Test loading config automatically if none provided
    mock_loaded_config = AuricConfig()
    mock_loaded_config.agents.defaults.logging.enabled = False
    
    # We patch the source of the late import
    with patch("auric.core.config.load_config", return_value=mock_loaded_config):
        sl = SystemLogger.get_instance()
        assert sl.config == mock_loaded_config
        assert sl._instance is not None


def test_system_logger_log_structured(mock_config, tmp_path):
    log_dir = tmp_path / "logs"
    mock_config.agents.defaults.logging.log_dir = str(log_dir)
    sl = SystemLogger(mock_config)
    
    event_data = {"key": "value"}
    sl.log("TEST_EVENT", event_data, session_id="sess_123", level="WARNING")
    
    log_file = log_dir / "system.jsonl"
    content = log_file.read_text(encoding="utf-8").strip()
    log_entry = json.loads(content)
    
    assert log_entry["event"] == "TEST_EVENT"
    assert log_entry["data"] == event_data
    assert log_entry["session_id"] == "sess_123"
    assert log_entry["level"] == "WARNING"
    assert "timestamp" in log_entry


def test_system_logger_log_disabled(mock_config, tmp_path):
    log_dir = tmp_path / "logs"
    mock_config.agents.defaults.logging.log_dir = str(log_dir)
    sl = SystemLogger(mock_config)
    
    # Disable after init
    sl.config.agents.defaults.logging.enabled = False
    sl.log("EVENT", {"data": 1})
    
    log_file = log_dir / "system.jsonl"
    # File might exist but should be empty (or at least not contain this event)
    if log_file.exists():
        assert log_file.stat().st_size == 0


def test_jsonl_formatter_dict():
    formatter = JSONLFormatter()
    record = MagicMock()
    payload = {"a": 1, "b": "c"}
    record.msg = payload
    
    result = formatter.format(record)
    assert json.loads(result) == payload


def test_jsonl_formatter_string():
    formatter = JSONLFormatter()
    record = MagicMock()
    record.msg = "Hello World"
    record.created = 1600000000.0
    record.levelname = "INFO"
    
    result = formatter.format(record)
    data = json.loads(result)
    assert data["event"] == "SYSTEM_MSG"
    assert data["data"]["message"] == "Hello World"
    assert data["level"] == "INFO"
    assert data["timestamp"] == datetime.fromtimestamp(1600000000.0).isoformat()


def test_system_logger_reinit_clears_handlers(mock_config, tmp_path):
    mock_config.agents.defaults.logging.log_dir = str(tmp_path / "logs")
    sl1 = SystemLogger(mock_config)
    assert len(sl1.logger.handlers) == 1
    
    sl2 = SystemLogger(mock_config)
    # Should still be 1, because handlers are cleared on init
    assert len(sl2.logger.handlers) == 1


def test_system_logger_relative_path(mock_config, tmp_path):
    # Mock cwd to a temp path
    with patch("auric.core.system_logger.Path.cwd", return_value=tmp_path):
        mock_config.agents.defaults.logging.log_dir = "rel_logs"
        # The code does log_dir = Path.cwd() / log_dir if not log_dir.is_absolute()
        sl = SystemLogger(mock_config)
        
        # Check handlers
        handler = next(h for h in sl.logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler))
        # baseFilename is absolute
        expected_path = (tmp_path / "rel_logs" / "system.jsonl").absolute()
        assert Path(handler.baseFilename).absolute() == expected_path
        assert expected_path.exists()
