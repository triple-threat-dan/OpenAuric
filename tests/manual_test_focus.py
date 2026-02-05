from pathlib import Path
from auric.memory.focus_manager import FocusManager, FocusState, ContextStaleError, FocusModel

TMP_FOCUS = Path("tmp_FOCUS.md")

def test_parsing():
    print("--- Testing Parsing ---")
    content = """# 🔮 THE FOCUS (Current State)

## 🎯 Prime Directive (The "Why")
User asked to scrape stocks.

## 📋 Plan of Action (The "How")
- [x] Step 1
- [ ] Step 2

## 🧠 Working Memory (Scratchpad)
Some notes.
"""
    TMP_FOCUS.write_text(content, encoding="utf-8")
    
    fm = FocusManager(TMP_FOCUS)
    model = fm.load()
    
    assert model.prime_directive == "User asked to scrape stocks.", f"Got: {model.prime_directive}"
    assert len(model.plan_steps) == 2
    assert model.plan_steps[0]['completed'] is True
    assert model.plan_steps[1]['completed'] is False
    assert model.working_memory == "Some notes.", f"Got: {model.working_memory}"
    assert model.state == FocusState.IN_PROGRESS
    
    print("✅ Parsing passed")

def test_missing_sections():
    print("--- Testing Robustness (Missing Sections) ---")
    content = """# Some Header
    
## 📋 Plan of Action (The "How")
- [ ] Only step
"""
    TMP_FOCUS.write_text(content, encoding="utf-8")
    fm = FocusManager(TMP_FOCUS)
    model = fm.load()
    
    assert model.prime_directive == ""
    assert len(model.plan_steps) == 1
    assert model.working_memory == ""
    assert model.state == FocusState.NEW
    print("✅ Robustness passed")

def test_interruption():
    print("--- Testing Interruption ---")
    fm = FocusManager(TMP_FOCUS)
    
    # Normal case
    fm.check_for_interrupt() # Should not raise
    
    # Interrupt
    fm.notify_user_edit()
    try:
        fm.check_for_interrupt()
        print("❌ Interruption FAILED: Did not raise ContextStaleError")
    except ContextStaleError:
        print("✅ Interruption passed (Raised correctly)")
        
    # Check reset
    fm.check_for_interrupt() # Should not raise again
    print("✅ Interruption reset passed")

def test_update_plan():
    print("--- Testing Update Plan ---")
    fm = FocusManager(TMP_FOCUS)
    new_model = FocusModel(
        prime_directive="New Goal",
        plan_steps=[{"step": "New Step", "completed": False}],
        working_memory="New Memory"
    )
    fm.update_plan(new_model)
    
    content = TMP_FOCUS.read_text("utf-8")
    assert "New Goal" in content
    assert "- [ ] New Step" in content
    assert "New Memory" in content
    print("✅ Update Plan passed")

if __name__ == "__main__":
    try:
        test_parsing()
        test_missing_sections()
        test_interruption()
        test_update_plan()
        print("\n🎉 ALL TESTS PASSED")
    finally:
        if TMP_FOCUS.exists():
            TMP_FOCUS.unlink()
