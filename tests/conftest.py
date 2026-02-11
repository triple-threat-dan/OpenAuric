import os
import json
import pytest
import asyncio
from typing import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """
    Sets up a temporary Auric configuration directory.
    Patches pathlib.Path.home() to return the tmp_path.
    """
    # Create the structure
    auric_dir = tmp_path / ".auric"
    auric_dir.mkdir()
    (auric_dir / "grimoire").mkdir()
    
    # Create dummy auric.json
    config_data = {
        "gateway": {"host": "127.0.0.1", "port": 8000},
        "agents": {"defaults": {"heartbeat": {"enabled": False}}}
    }
    (auric_dir / "auric.json").write_text(json.dumps(config_data), encoding="utf-8")
    
    # Create dummy Markdown files
    (auric_dir / "grimoire" / "SOUL.md").write_text("# SOUL", encoding="utf-8")
    (auric_dir / "grimoire" / "FOCUS.md").write_text("""# 🔮 THE FOCUS
## 🎯 Prime Directive
Do the thing.

## 📋 Plan of Action
- [ ] Task 1

## 🧠 Working Memory
None.
""", encoding="utf-8")
    
    # Patch Path.home() to return tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    
    return auric_dir

@pytest.fixture
def mock_tui():
    """
    Returns a Mock object mimicking the AuricTUI app.
    """
    mock = MagicMock()
    mock.run_async = AsyncMock(return_value=None)
    return mock

@pytest.fixture
def mock_config_obj():
    """
    Returns a MagicMock mimicking AuricConfig.
    Useful for unit tests that pass config directly.
    """
    mock = MagicMock()
    # Setup common nested attributes to avoid AttributeError
    mock.agents.defaults.heartbeat.enabled = True
    mock.sandbox.allowed_imports = []
    # Add others as needed
    return mock
