import re
import threading
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from auric.core.config import AURIC_ROOT


class ContextStaleError(Exception):
    """Raised when the agent's context is invalid due to user intervention."""


class FocusState(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class FocusModel(BaseModel):
    prime_directive: str = Field(description="The high-level goal or 'Why'.")
    plan_steps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of steps, e.g., [{'step': 'Do X', 'completed': False}]"
    )
    working_memory: str = Field(default="", description="Scratchpad notes.")
    focus_path: Path = Field(
        default_factory=lambda: AURIC_ROOT / "memories" / "FOCUS.md",
        description="Path to the focus file."
    )
    state: FocusState = Field(default=FocusState.NEW, description="Derived state of the focus.")

    def get_active_step(self) -> Optional[str]:
        """Returns the text of the first incomplete step."""
        for step in self.plan_steps:
            if not step.get("completed", False):
                return step.get("step")
        return None


class FocusManager:
    """
    Manages the 'Working Memory' of the agent by syncing with a Markdown file.
    Acts as a synchronization primitive between Human and AI.
    """

    DEFAULT_TEMPLATE = """# 🔮 THE FOCUS (Current State)

## 🎯 Prime Directive (The "Why")
(Waiting for command...)

## 📋 Plan of Action (The "How")
- [ ] Await instructions

## 🧠 Working Memory (Scratchpad)
- System initialized.
"""

    # Pre-compiled regex patterns — avoids recompilation on every parse call.
    _RE_PRIME = re.compile(r'## 🎯 Prime Directive.*?\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
    _RE_PLAN = re.compile(r'## 📋 Plan of Action.*?\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
    _RE_MEMORY = re.compile(r'## 🧠 Working Memory.*?\n(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE)
    _RE_STEP = re.compile(r'-\s*\[([ xX])\]\s*(.*)')

    def __init__(self, focus_file_path: Path):
        self._focus_file_path = focus_file_path
        self._is_stale = False
        self._lock = threading.Lock()

    def notify_user_edit(self):
        """Signal that the user has modified the focus file manually."""
        with self._lock:
            self._is_stale = True

    def check_for_interrupt(self):
        """Checks if the context is stale. If so, raises ContextStaleError to restart reasoning."""
        with self._lock:
            if self._is_stale:
                self._is_stale = False
                raise ContextStaleError("Focus file was modified by user. Restarting context.")

    def clear(self):
        """Resets the focus file to the default idle state."""
        self._write_to_file(self.DEFAULT_TEMPLATE)

    def load(self) -> FocusModel:
        """Parses the FOCUS.md file into a structured model. Robust to missing sections."""
        return self._parse_markdown(self._read_from_file())

    def update_plan(self, new_plan: FocusModel):
        """Serializes the model to Markdown and overwrites FOCUS.md."""
        self._write_to_file(self._serialize_model(new_plan))

    def _read_from_file(self) -> str:
        if not self._focus_file_path.exists():
            return ""
        return self._focus_file_path.read_text(encoding="utf-8")

    def _write_to_file(self, content: str):
        self._focus_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._focus_file_path.write_text(content, encoding="utf-8")

    def _parse_markdown(self, content: str) -> FocusModel:
        """Parses raw markdown content into a FocusModel."""
        prime_match = self._RE_PRIME.search(content)
        plan_match = self._RE_PLAN.search(content)
        memory_match = self._RE_MEMORY.search(content)

        prime_directive = prime_match.group(1).strip() if prime_match else ""
        raw_plan = plan_match.group(1).strip() if plan_match else ""
        working_memory = memory_match.group(1).strip() if memory_match else ""

        # Parse plan steps from checkbox lines
        steps = []
        for line in raw_plan.split('\n'):
            match = self._RE_STEP.match(line.strip())
            if match:
                steps.append({
                    "step": match.group(2).strip(),
                    "completed": match.group(1).lower() == 'x',
                })

        # Derive state in a single pass over the completed flags
        if not steps:
            state = FocusState.NEW
        else:
            completed = {s['completed'] for s in steps}
            if completed == {True}:
                state = FocusState.COMPLETE
            elif completed == {False}:
                state = FocusState.NEW
            else:
                state = FocusState.IN_PROGRESS

        return FocusModel(
            prime_directive=prime_directive,
            plan_steps=steps,
            working_memory=working_memory,
            state=state,
        )

    def _serialize_model(self, model: FocusModel) -> str:
        """Converts FocusModel back to the specific Markdown format."""
        directive = model.prime_directive or "(No directive set)"
        memory = model.working_memory or "(Empty)"

        if model.plan_steps:
            plan_lines = [
                f"- [{'x' if s['completed'] else ' '}] {s['step']}"
                for s in model.plan_steps
            ]
        else:
            plan_lines = ["- [ ] (No steps defined)"]

        return "\n".join([
            '# 🔮 THE FOCUS (Current State)',
            '',
            '## 🎯 Prime Directive (The "Why")',
            directive,
            '',
            '## 📋 Plan of Action (The "How")',
            *plan_lines,
            '',
            '## 🧠 Working Memory (Scratchpad)',
            memory,
            '',
        ])
