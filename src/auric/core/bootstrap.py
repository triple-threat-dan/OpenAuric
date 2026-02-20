"""
Bootstrap module for initializing the OpenAuric workspace.

Ensures that the .auric directory structure exists, populates it with default templates
(AGENT.md, etc.) if missing, and installs default spells into the grimoire.
"""

import logging
import shutil
from pathlib import Path

from auric.core.config import AURIC_ROOT, AURIC_TEMPLATES_DIR, AURIC_WORKSPACE_DIR

logger = logging.getLogger("auric.bootstrap")

# Constants
PKG_ROOT = Path(__file__).resolve().parent.parent 
DEFAULT_SPELLS_DIR = PKG_ROOT / "spells" / "default"

FILES_TO_COPY = {
    "AGENT.md": "AGENT.md",
    "HEARTBEAT.md": "HEARTBEAT.md",
    "SOUL.md": "SOUL.md",
    "USER.md": "USER.md",
    "memories/FOCUS.md": "memories/FOCUS.md",
    "memories/MEMORY.md": "memories/MEMORY.md"
}

def ensure_workspace() -> None:
    """
    Ensures that the auric root directory exists and is populated with necessary files.
    Copies from templates if they don't exist in root.
    Installs default spells if missing.
    """
    if not AURIC_ROOT.exists():
        logger.info(f"Creating auric root at {AURIC_ROOT}")
        AURIC_ROOT.mkdir(parents=True, exist_ok=True)

    # Ensure subdirectories exist
    (AURIC_ROOT / "grimoire").mkdir(exist_ok=True)
    (AURIC_ROOT / "memories").mkdir(exist_ok=True)
    (AURIC_ROOT / "workspace").mkdir(exist_ok=True)

    if not AURIC_TEMPLATES_DIR.exists():
        logger.warning(f"Templates directory not found at {AURIC_TEMPLATES_DIR}. Cannot bootstrap workspace.")
        return

    for src_rel, dest_rel in FILES_TO_COPY.items():
        source = AURIC_TEMPLATES_DIR / src_rel
        target = AURIC_ROOT / dest_rel

        if not target.exists():
            if source.exists():
                logger.info(f"Bootstrapping {src_rel}...")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                logger.warning(f"Template {src_rel} missing in {AURIC_TEMPLATES_DIR}")

    # Copy Default Spells
    TARGET_SPELLS_DIR = AURIC_ROOT / "grimoire"

    if DEFAULT_SPELLS_DIR.exists():
        logger.info("Installing default spells...")
        if not TARGET_SPELLS_DIR.exists():
            TARGET_SPELLS_DIR.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.copytree(DEFAULT_SPELLS_DIR, TARGET_SPELLS_DIR, dirs_exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to install default spells: {e}")
    else:
        logger.warning(f"Default spells directory not found at {DEFAULT_SPELLS_DIR}")
