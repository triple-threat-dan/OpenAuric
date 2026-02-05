
import asyncio
import os
import time
from pathlib import Path
from auric.core.heartbeat import HeartbeatManager, can_dream, run_dream_cycle_task

async def main():
    print("=== Testing HeartbeatManager ===")
    
    # Test Singleton
    hb1 = HeartbeatManager.get_instance()
    hb2 = HeartbeatManager.get_instance()
    assert hb1 is hb2, "Singleton check failed"
    print("✅ Singleton verified")

    # Test Touch & Idle
    print(f"Initial Last Active: {hb1.last_active}")
    time.sleep(0.1)
    hb1.touch()
    print(f"Touched Last Active: {hb1.last_active}")
    
    # Test Idle Logic (Micro threshold)
    # We can't really wait 30 minutes, so we'll test the logic with a tiny threshold manually
    # But the class hardcodes default, so we pass argument
    assert not hb1.is_idle(threshold_minutes=1), "Should not be idle immediately"
    print("✅ Not idle immediately verified")
    
    # Mocking time passage would require mocking datetime, which is complex in a simple script.
    # Instead, we will rely on logic review for the timedelta, but we can verify the 'is_idle' 
    # returns True if we set `_last_active_timestamp` significantly back.
    from datetime import datetime, timedelta
    hb1._last_active_timestamp = datetime.now() - timedelta(minutes=31)
    assert hb1.is_idle(threshold_minutes=30), "Should be idle after 31 mins"
    print("✅ Idle detection verified (via timestamp manipulation)")

    # Test Dream Cycle Logic
    print("\n=== Testing Dream Cycle Logic ===")
    
    # 1. Idle is True (from above)
    # 2. Need log file
    log_dir = Path.home() / ".auric" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "current_session.log"
    
    # Case A: No file / Empty file
    if log_file.exists():
        log_file.unlink()
    
    assert can_dream() is False, "Should not dream if log missing"
    print("✅ Dream blocked (No log)")
    
    log_file.touch()
    assert can_dream() is False, "Should not dream if log empty"
    print("✅ Dream blocked (Empty log)")
    
    # Case B: Valid File
    with open(log_file, "w") as f:
        f.write("Some log data...")
    
    # We are idle (31 mins back) AND have data
    assert can_dream() is True, "Should dream now"
    print("✅ Dream Allowed (Idle + Data)")

    # Case C: Not Idle
    hb1.touch() # Reset to now
    assert can_dream() is False, "Should not dream if active"
    print("✅ Dream blocked (User active)")

    # Async Task Test (Dry Run)
    print("\n=== Running Async Task Wrapper ===")
    await run_dream_cycle_task() # Should skip because active
    
    # Force run
    hb1._last_active_timestamp = datetime.now() - timedelta(minutes=31)
    await run_dream_cycle_task() # Should run stub
    
    print("\n✅ All Checks Passed")

if __name__ == "__main__":
    asyncio.run(main())
