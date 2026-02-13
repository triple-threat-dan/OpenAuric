import sys
import json
import os
import argparse
from pathlib import Path

def create_spell(name: str, target_dir: Path):
    """Creates a new spell scaffold."""
    spell_path = target_dir / name
    
    if spell_path.exists():
        print(f"Error: Spell '{name}' already exists at {spell_path}")
        return

    try:
        spell_path.mkdir(parents=True, exist_ok=True)
        (spell_path / "scripts").mkdir(exist_ok=True)
        (spell_path / "references").mkdir(exist_ok=True)
        (spell_path / "assets").mkdir(exist_ok=True)

        skill_md = f"""---
name: {name}
description: TODO: Brief specific description (1 sentence).
---

# {name}

## Overview

[TODO: What this spell does]

## Instructions

[TODO: Step-by-step instructions]

## Resources

- **scripts/**: Executable code
- **references/**: Contextual docs
- **assets/**: Output templates
"""
        (spell_path / "SKILL.md").write_text(skill_md, encoding="utf-8")
        
        # Create a default run.py
        run_py = """import sys
import json

# Arguments are passed as a JSON string in argv[1]
try:
    args = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    args = {}

# TODO: Implement spell logic here
print(f"Spell executed with args: {args}")
"""
        (spell_path / "scripts" / "run.py").write_text(run_py, encoding="utf-8")

        print(f"Successfully created spell '{name}' at {spell_path}")

    except Exception as e:
        print(f"Error created spell: {e}")

if __name__ == "__main__":
    # RLM acts as the user, sending arguments.
    # The first argument is the script path, the second is the JSON args string.
    # python init_skill.py '{"name": "foo"}'
    
    if len(sys.argv) < 2:
        print("Usage: python init_skill.py <json_args>")
        sys.exit(1)

    try:
        # Check if first arg is JSON or just a string name (handling relaxed input)
        input_arg = sys.argv[1]
        
        if input_arg.strip().startswith("{"):
            args = json.loads(input_arg)
            name = args.get("name")
            # Optional path override, default to Grimoire
            custom_path = args.get("path")
        else:
            # Assume it's just the name
            name = input_arg
            custom_path = None
            
        if not name:
            print("Error: 'name' is required.")
            sys.exit(1)

        # Default to ~/.auric/grimoire/spells if no path provided
        if custom_path:
             target_dir = Path(custom_path).expanduser()
        else:
             target_dir = Path("~/.auric/grimoire/spells").expanduser()

        create_spell(name, target_dir)

    except json.JSONDecodeError:
         print("Error: Invalid JSON arguments.")
         sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)