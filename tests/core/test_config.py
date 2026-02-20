import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from auric.core import config

# Create aliases for clarity in tests
AuricConfig = config.AuricConfig
ConfigLoader = config.ConfigLoader
SecretsManager = config.SecretsManager

@pytest.fixture
def mock_cwd(tmp_path):
    """Fixture to provide a temporary current working directory."""
    with patch("auric.core.config.Path.cwd", return_value=tmp_path):
        yield tmp_path

@pytest.fixture
def mock_auric_root(tmp_path):
    """Fixture to mock AURIC_ROOT to a temporary path."""
    auric_root = tmp_path / ".auric"
    with patch("auric.core.config.AURIC_ROOT", auric_root), \
         patch.object(ConfigLoader, "DEFAULT_CONFIG_DIR", auric_root):
        yield auric_root

@pytest.fixture(autouse=True)
def reset_globals():
    """Reset global state between tests."""
    config._params = None
    config._secrets = None
    yield
    config._params = None
    config._secrets = None

# ==============================================================================
# Tests for find_auric_root
# ==============================================================================

def test_find_auric_root_in_cwd(mock_cwd):
    """Test find_auric_root when .auric is in cwd."""
    (mock_cwd / ".auric").mkdir()
    assert config.find_auric_root() == mock_cwd / ".auric"

def test_find_auric_root_in_parent(mock_cwd):
    """Test find_auric_root when .auric is in a parent directory."""
    parent_dir = mock_cwd / "parent"
    child_dir = parent_dir / "child"
    child_dir.mkdir(parents=True)
    
    (parent_dir / ".auric").mkdir()
    
    with patch("auric.core.config.Path.cwd", return_value=child_dir):
        assert config.find_auric_root() == parent_dir / ".auric"

def test_find_auric_root_not_found(mock_cwd):
    """Test find_auric_root when .auric is nowhere to be found."""
    # We must mock Path.exists to always return False, otherwise it might find
    # a real .auric in a parent directory (e.g. ~/.auric) since the temp path
    # is usually inside the user's home directory.
    with patch("auric.core.config.Path.exists", return_value=False):
        assert config.find_auric_root() == mock_cwd / ".auric"

# ==============================================================================
# Tests for ConfigLoader
# ==============================================================================

def test_config_loader_get_config_path(mock_auric_root):
    expected_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    assert ConfigLoader.get_config_path() == expected_path

def test_config_loader_ensure_permissions_creates_parent(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    assert not mock_auric_root.exists()
    
    ConfigLoader._ensure_permissions(config_path)
    
    assert mock_auric_root.exists()

def test_config_loader_ensure_permissions_parent_creation_fails(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    
    with patch("pathlib.Path.mkdir", side_effect=Exception("mkdir failed")):
        with pytest.raises(Exception, match="mkdir failed"):
            ConfigLoader._ensure_permissions(config_path)

def test_config_loader_ensure_permissions_changes_mode(mock_auric_root, caplog):
    import logging
    caplog.set_level(logging.INFO)
    
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir()
    config_path.touch()
    
    # We must patch sys.platform because the logic bypasses chmod on win32
    with patch("sys.platform", "linux"):
        with patch("os.stat") as mock_stat, \
             patch("auric.core.config.os.chmod") as mock_chmod:
            mock_stat_obj = MagicMock()
            mock_stat_obj.st_mode = 0o100644 # Has group/other read permissions
            mock_stat.return_value = mock_stat_obj
            
            with patch.object(Path, "stat", return_value=mock_stat_obj):
                ConfigLoader._ensure_permissions(config_path)
    
    # Assert that os.chmod was called to set to 0o600
    mock_chmod.assert_called_once_with(config_path, 0o600)
    assert f"Fixed permissions for {config_path} to 0600." in caplog.text

def test_config_loader_ensure_permissions_chmod_fails(mock_auric_root, caplog):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir()
    config_path.touch()
    
    with patch("os.chmod", side_effect=Exception("chmod failed")):
        # We also need to mock sys.platform if it's win32 since it skips chmod inside the method
        with patch("sys.platform", "linux"):
             # Set mode manually so it triggers chmod attempt
             with patch("os.stat") as mock_stat:
                 mock_stat_obj = MagicMock()
                 mock_stat_obj.st_mode = 0o100644
                 mock_stat.return_value = mock_stat_obj
                 with patch("pathlib.Path.stat", return_value=mock_stat_obj):
                     ConfigLoader._ensure_permissions(config_path)
    
    assert "Could not enforce permissions" in caplog.text

def test_config_loader_load_creates_default(mock_auric_root, caplog):
    import logging
    caplog.set_level(logging.INFO)
    
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    assert not config_path.exists()
    
    loaded_config = ConfigLoader.load()
    
    assert isinstance(loaded_config, AuricConfig)
    assert config_path.exists()
    assert "Creating default" in caplog.text

def test_config_loader_load_existing_valid(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir(parents=True, exist_ok=True)
    
    # Write some valid JSON5
    config_content = '{"debug": true}'
    config_path.write_text(config_content, encoding="utf-8")
    
    loaded_config = ConfigLoader.load()
    
    assert isinstance(loaded_config, AuricConfig)
    assert loaded_config.debug is True

def test_config_loader_load_existing_invalid(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir(parents=True, exist_ok=True)
    
    # Write invalid JSON
    config_path.write_text("{invalid json}", encoding="utf-8")
    
    with pytest.raises(ValueError, match="Invalid configuration file"):
        ConfigLoader.load()

def test_config_loader_save_new_file(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    assert not config_path.exists()
    
    new_config = AuricConfig(debug=True)
    ConfigLoader.save(new_config)
    
    assert config_path.exists()
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["debug"] is True

def test_config_loader_save_existing_file(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    
    new_config = AuricConfig(debug=True)
    ConfigLoader.save(new_config)
    
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["debug"] is True

def test_config_loader_save_existing_file_chmod_fails(mock_auric_root):
    config_path = mock_auric_root / ConfigLoader.CONFIG_FILENAME
    mock_auric_root.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    
    new_config = AuricConfig(debug=True)
    with patch("auric.core.config.os.chmod", side_effect=Exception("chmod failed")):
        ConfigLoader.save(new_config)
        
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["debug"] is True

def test_config_loader_save_fails(mock_auric_root):
    new_config = AuricConfig()
    with patch("auric.core.config.os.open", side_effect=Exception("open failed")):
        with pytest.raises(Exception, match="open failed"):
            ConfigLoader.save(new_config)

# ==============================================================================
# Tests for SecretsManager
# ==============================================================================

def test_secrets_manager_get_secret():
    test_config = AuricConfig()
    test_config.keys.openai = "test-key"
    test_config.tools = {"custom_tool": {"secret": "tool-secret"}}
    
    sm = SecretsManager(test_config)
    
    assert sm.get_secret("keys.openai") == "test-key"
    assert sm.get_secret("tools.custom_tool.secret") == "tool-secret"

def test_secrets_manager_get_secret_not_found():
    test_config = AuricConfig()
    sm = SecretsManager(test_config)
    
    assert sm.get_secret("keys.nonexistent") is None
    assert sm.get_secret("does.not.exist") is None

def test_secrets_manager_get_secret_not_string():
    test_config = AuricConfig()
    test_config.tools = {"custom_tool": {"complex": {"nested": "dict"}}}
    
    sm = SecretsManager(test_config)
    # The value is a dictionary, not string/int/float/bool
    assert sm.get_secret("tools.custom_tool.complex") is None

# ==============================================================================
# Tests for Global Accessors
# ==============================================================================

def test_load_config(mock_auric_root):
    loaded = config.load_config()
    assert isinstance(loaded, AuricConfig)
    # Consecutive calls return the same instance
    assert config.load_config() is loaded

def test_get_secrets_manager(mock_auric_root):
    sm = config.get_secrets_manager()
    assert isinstance(sm, SecretsManager)
    # Consecutive calls return the same instance
    assert config.get_secrets_manager() is sm


def test_get_secrets_manager_initialization_fails():
    """Test when SecretsManager unexpectedly fails to initialize."""
    config._secrets = None
    config._params = None
    
    # Force load_config to set _secrets to None (which it wouldn't normally do)
    with patch("auric.core.config.load_config") as mock_load:
        def side_effect():
            config._secrets = None
        mock_load.side_effect = side_effect
        
        with pytest.raises(RuntimeError, match="Failed to initialize SecretsManager"):
            config.get_secrets_manager()
