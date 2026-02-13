import logging
import shutil
from pathlib import Path
from auric.core.config import AURIC_ROOT, AURIC_TEMPLATES_DIR, AURIC_WORKSPACE_DIR

logger = logging.getLogger("auric.bootstrap")

def ensure_workspace():
    """
    Ensures that the auric root directory exists and is populated with necessary files.
    Copies from templates if they don't exist in root.
    """
    print(f"DEBUG: Checking auric root at {AURIC_ROOT}")
    if not AURIC_ROOT.exists():
        print(f"DEBUG: Creating auric root directory: {AURIC_ROOT}")
        logger.info(f"Creating auric root at {AURIC_ROOT}")
        AURIC_ROOT.mkdir(parents=True, exist_ok=True)

    # Ensure subdirectories exist
    (AURIC_ROOT / "grimoire").mkdir(exist_ok=True)
    (AURIC_ROOT / "memories").mkdir(exist_ok=True)
    (AURIC_ROOT / "workspace").mkdir(exist_ok=True) # Keep workspace dir for agent output

    # Define the structure to copy
    # Mapping: Source relative to AURIC_TEMPLATES_DIR -> Target relative to AURIC_ROOT
    files_to_copy = {
        "AGENT.md": "AGENT.md",
        "HEARTBEAT.md": "HEARTBEAT.md",
        "SOUL.md": "SOUL.md",
        "USER.md": "USER.md",
        "grimoire/SPELLS.md": "grimoire/SPELLS.md",
        "memories/FOCUS.md": "memories/FOCUS.md",
        "memories/MEMORY.md": "memories/MEMORY.md"
    }

    if not AURIC_TEMPLATES_DIR.exists():
        logger.warning(f"Templates directory not found at {AURIC_TEMPLATES_DIR}. Cannot bootstrap workspace.")
        return

    for src_rel, dest_rel in files_to_copy.items():
        source = AURIC_TEMPLATES_DIR / src_rel
        target = AURIC_ROOT / dest_rel

        if not target.exists():
            if source.exists():
                print(f"DEBUG: Bootstrapping {src_rel} to {target}")
                logger.info(f"Bootstrapping {src_rel}...")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                logger.warning(f"Template {src_rel} missing in {AURIC_TEMPLATES_DIR}")

    # Copy Default Spells
    # Spells are located in src/auric/spells/default -> ~/.auric/grimoire/
    # Note: We now flatten the spells structure, so they go directly into grimoire/
    
    # Locate src/auric/spells/default relative to this file
    # bootstrap.py is in src/auric/core/
    PKG_ROOT = Path(__file__).resolve().parent.parent 
    DEFAULT_SPELLS_DIR = PKG_ROOT / "spells" / "default"
    TARGET_SPELLS_DIR = AURIC_ROOT / "grimoire"

    if DEFAULT_SPELLS_DIR.exists():
        print(f"DEBUG: Installing default spells from {DEFAULT_SPELLS_DIR} to {TARGET_SPELLS_DIR}")
        logger.info("Installing default spells...")
        if not TARGET_SPELLS_DIR.exists():
            TARGET_SPELLS_DIR.mkdir(parents=True, exist_ok=True)
        
        # We use copytree with dirs_exist_ok=True to merge/update
        # But copytree copies the directory itself if we point to it? 
        # No, copytree(src, dst) copies content of src into dst if dst exists and dirs_exist_ok.
        # However, default contains subfolders (e.g. spell-crafter). We want those subfolders in grimoire.
        shutil.copytree(DEFAULT_SPELLS_DIR, TARGET_SPELLS_DIR, dirs_exist_ok=True)
    else:
        # It's possible we are running from an installed package where spells/default might not be present 
        # if not included in package data, but let's assume it is.
        logger.warning(f"Default spells directory not found at {DEFAULT_SPELLS_DIR}")
