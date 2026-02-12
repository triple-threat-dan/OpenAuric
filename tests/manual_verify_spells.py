import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path.cwd() / "src"))

from auric.core.config import load_config, ConfigLoader
from auric.spells.tool_registry import ToolRegistry

async def main():
    print("--- Verifying Spells System ---")
    
    # 1. Load Config & Registry
    try:
        config = load_config()
        registry = ToolRegistry(config)
        print("[OK] Registry initialized.")
    except Exception as e:
        print(f"[FAIL] Registry init failed: {e}")
        import traceback
        traceback.print_exc()
        return

    # 2. Check Loaded Spells (powershell should NOT be here)
    spells = registry._spells
    print(f"Loaded Spells: {list(spells.keys())}")
    
    if "powershell" in spells:
        print("[FAIL] 'powershell' spell found in external spells (should be internal).")
    else:
        print("[OK] 'powershell' spell correctly absent from external spells.")
        
    if "spell-crafter" not in spells:
        print("[FAIL] 'spell-crafter' spell not found.")
    else:
        print("[OK] 'spell-crafter' spell found.")

    # 3. Check Internal Tools
    internal_tools = registry._internal_tools
    if "execute_powershell" in internal_tools:
        print("[OK] 'execute_powershell' found in internal tools.")
    else:
        print("[FAIL] 'execute_powershell' NOT found in internal tools.")

    if "run_python" in internal_tools:
        print("[OK] 'run_python' found in internal tools.")
    else:
        print("[FAIL] 'run_python' NOT found in internal tools.")

    # 4. Test PowerShell Internal Tool
    print("\nExecuting 'execute_powershell' tool...")
    try:
        # Simple echo
        result = await registry.execute_tool("execute_powershell", {"command": "echo 'Hello from Internal Tool'"})
        print(f"Result: {result}")
        if "Hello from Internal Tool" in result:
             print("[OK] PowerShell execution successful.")
        else:
             print("[FAIL] PowerShell output mismatch.")
    except Exception as e:
        print(f"[FAIL] PowerShell execution error: {e}")

    # 5. Test Python Sandbox
    print("\nExecuting 'run_python' tool...")
    try:
        # Simple math
        result = await registry.execute_tool("run_python", {"code": "print(10 + 20)"})
        print(f"Result: {result}")
        if "30" in result:
             print("[OK] Python execution successful.")
        else:
             print("[FAIL] Python output mismatch.")
    except Exception as e:
        print(f"[FAIL] Python execution error: {e}")

    # 6. Check SPELLS.md
    spells_md = Path("~/.auric/grimoire/SPELLS.md").expanduser()
    if spells_md.exists():
        content = spells_md.read_text(encoding="utf-8")
        # should NOT contain ## powershell
        # Note: Depending on bootstrap timing, the file might still have old content if we didn't wipe it.
        # But we deleted it from templates/grimoire/SPELLS.md, so only new installs or if we overwrite
        # the workspace file manually will show it gone.
        # The registry generates SPELLS.md on load, so it should be gone from there too if load_spells was run.
        if "## powershell" not in content:
            print("[OK] SPELLS.md does not contain powershell.")
        else:
            print(f"[WARN] SPELLS.md still contains powershell (might be acceptable if not re-generated).")
            
        if "## spell-crafter" in content:
            print("[OK] SPELLS.md contains spell-crafter.")
    else:
        print(f"\n[FAIL] SPELLS.md not found at {spells_md}")

    # 7. Check Spell Context Injection
    print("\nChecking Spell Context Injection...")
    # We call 'spell-crafter' which is an instruction spell
    spell_instruction = await registry.execute_tool("spell-crafter", {})
    if "## Spell Context" in spell_instruction and "**Path**:" in spell_instruction:
        print("[OK] Spell Context injected into instruction.")
        # print(f"Sample Context: {spell_instruction.split('## Spell Context')[1].strip()[:100]}...")
    else:
        print(f"[FAIL] Spell Context NOT injected.\nOutput: {spell_instruction[:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
