import logging
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from auric.core import bootstrap

@pytest.fixture
def mock_paths(tmp_path):
    """Fixture to set up temporary directories for testing bootstrap."""
    auric_root = tmp_path / ".auric"
    templates_dir = tmp_path / "templates"
    default_spells = tmp_path / "default_spells"
    
    # Setup template files
    templates_dir.mkdir(parents=True, exist_ok=True)
    for src, _ in bootstrap.FILES_TO_COPY.items():
        file_path = templates_dir / src
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f"mock content for {src}")
    
    # Setup default spells
    default_spells.mkdir(parents=True, exist_ok=True)
    (default_spells / "spell.py").write_text("print('mock spell')")

    # Patch the constants used in bootstrap.py
    with patch("auric.core.bootstrap.AURIC_ROOT", auric_root), \
         patch("auric.core.bootstrap.AURIC_TEMPLATES_DIR", templates_dir), \
         patch("auric.core.bootstrap.DEFAULT_SPELLS_DIR", default_spells):
        yield {"root": auric_root, "templates": templates_dir, "spells": default_spells}


def test_ensure_workspace_success(mock_paths, caplog):
    """Test successful creation of the workspace from scratch."""
    caplog.set_level(logging.INFO)
    
    bootstrap.ensure_workspace()
    
    root = mock_paths["root"]
    
    # Ensure auric root and expected subdirectories exist
    assert root.exists()
    assert (root / "grimoire").exists()
    assert (root / "memories").exists()
    assert (root / "workspace").exists()
    
    # Check that all templates were copied correctly
    for src, dest in bootstrap.FILES_TO_COPY.items():
        dest_path = root / dest
        assert dest_path.exists()
        assert dest_path.read_text() == f"mock content for {src}"
        
    # Check that default spells were installed
    spell_path = root / "grimoire" / "spell.py"
    assert spell_path.exists()
    assert spell_path.read_text() == "print('mock spell')"

    assert f"Creating auric root at {root}" in caplog.text


def test_ensure_workspace_already_exists(mock_paths, caplog):
    """Test behavior when the workspace already exists."""
    caplog.set_level(logging.INFO)
    
    # Run once to completely setup
    bootstrap.ensure_workspace()
    caplog.clear()
    
    # Modify a template file in the destination to ensure it's not overwritten
    root = mock_paths["root"]
    agent_msg = root / "AGENT.md"
    agent_msg.write_text("custom agent content")
    
    # Run again
    bootstrap.ensure_workspace()
    
    # The custom content should be untouched since the file already exists
    assert agent_msg.read_text() == "custom agent content"
    assert "Creating auric root" not in caplog.text


def test_ensure_workspace_templates_dir_missing(mock_paths, caplog):
    """Test warning when templates directory is completely missing."""
    shutil.rmtree(mock_paths["templates"])
    
    bootstrap.ensure_workspace()
    
    # Check that warning was logged
    assert "Templates directory not found" in caplog.text
    # The root should be created, but no templates copied
    assert mock_paths["root"].exists()
    assert not (mock_paths["root"] / "AGENT.md").exists()


def test_ensure_workspace_partial_templates_missing(mock_paths, caplog):
    """Test warning when only some templates are missing in the template directory."""
    agent_template = mock_paths["templates"] / "AGENT.md"
    agent_template.unlink()  # Remove just one template
    
    bootstrap.ensure_workspace()
    
    root = mock_paths["root"]
    assert "Template AGENT.md missing" in caplog.text
    # Check that it did NOT copy AGENT.md
    assert not (root / "AGENT.md").exists()
    # Check that it DID copy others, like HEARTBEAT.md
    assert (root / "HEARTBEAT.md").exists()


def test_ensure_workspace_default_spells_missing(mock_paths, caplog):
    """Test warning when default spells directory is missing."""
    shutil.rmtree(mock_paths["spells"])
    
    bootstrap.ensure_workspace()
    
    assert "Default spells directory not found" in caplog.text
    # Grimoire directory should still be created
    assert (mock_paths["root"] / "grimoire").exists()


def test_ensure_workspace_default_spells_copy_fails(mock_paths, caplog):
    """Test error handling when copying default spells raises an exception."""
    with patch("auric.core.bootstrap.shutil.copytree", side_effect=Exception("Mocked copy error")):
        bootstrap.ensure_workspace()
        
    assert "Failed to install default spells: Mocked copy error" in caplog.text


def test_ensure_workspace_target_spells_dir_recreated(mock_paths, caplog):
    """
    Test recreating TARGET_SPELLS_DIR if it's missing just before the explicit existence check.
    We mock Path.exists to return False specifically for the grimoire directory.
    """
    target_spells_dir = mock_paths["root"] / "grimoire"
    
    original_exists = Path.exists
    
    def mock_exists(self):
        # When checking if TARGET_SPELLS_DIR exists on line 65, return False
        # to trigger line 66
        if self == target_spells_dir:
            return False
        return original_exists(self)
        
    with patch.object(Path, "exists", side_effect=mock_exists, autospec=True):
        bootstrap.ensure_workspace()
        
    assert target_spells_dir.exists()
