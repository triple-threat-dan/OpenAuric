import logging
import shutil
from pathlib import Path
from auric.core.config import AURIC_WORKSPACE_DIR, AURIC_TEMPLATES_DIR

logger = logging.getLogger("auric.bootstrap")

def ensure_workspace():
    """
    Ensures that the workspace directory exists and is populated with necessary files.
    Copies from templates if they don't exist in workspace.
    """
    print(f"DEBUG: Checking workspace at {AURIC_WORKSPACE_DIR}")
    if not AURIC_WORKSPACE_DIR.exists():
        print(f"DEBUG: Creating workspace directory: {AURIC_WORKSPACE_DIR}")
        logger.info(f"Creating workspace at {AURIC_WORKSPACE_DIR}")
        AURIC_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

    # Define the structure to copy
    # Source relative to AURIC_TEMPLATES_DIR -> Target relative to AURIC_WORKSPACE_DIR
    files_to_copy = [
        "AGENT.md",
        "HEARTBEAT.md",
        "SOUL.md",
        "USER.md",
        "grimoire/SPELLS.md",
        "grimoire/FOCUS.md",
        "grimoire/MEMORY.md"
    ]

    if not AURIC_TEMPLATES_DIR.exists():
        logger.warning(f"Templates directory not found at {AURIC_TEMPLATES_DIR}. Cannot bootstrap workspace.")
        return

    for relative_path in files_to_copy:
        source = AURIC_TEMPLATES_DIR / relative_path
        target = AURIC_WORKSPACE_DIR / relative_path

        if not target.exists():
            if source.exists():
                print(f"DEBUG: Bootstrapping {relative_path} to {target}")
                logger.info(f"Bootstrapping {relative_path}...")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                logger.warning(f"Template {relative_path} missing in {AURIC_TEMPLATES_DIR}")

    # Copy Default Spells
    # Spells are located in src/auric/spells/default -> ~/.auric/grimoire/spells
    
    # Locate src/auric/spells/default relative to this file
    # bootstrap.py is in src/auric/core/
    PKG_ROOT = Path(__file__).resolve().parent.parent 
    DEFAULT_SPELLS_DIR = PKG_ROOT / "spells" / "default"
    TARGET_SPELLS_DIR = AURIC_WORKSPACE_DIR / "grimoire" / "spells"

    if DEFAULT_SPELLS_DIR.exists():
        print(f"DEBUG: Installing default spells from {DEFAULT_SPELLS_DIR} to {TARGET_SPELLS_DIR}")
        logger.info("Installing default spells...")
        if not TARGET_SPELLS_DIR.exists():
            TARGET_SPELLS_DIR.mkdir(parents=True, exist_ok=True)
        
        # We use copytree with dirs_exist_ok=True to merge/update
        shutil.copytree(DEFAULT_SPELLS_DIR, TARGET_SPELLS_DIR, dirs_exist_ok=True)
    else:
        logger.warning(f"Default spells directory not found at {DEFAULT_SPELLS_DIR}")
