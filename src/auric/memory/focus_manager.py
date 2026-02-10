import re
import threading
from enum import Enum
from datetime import datetime
from auric.core.config import AURIC_WORKSPACE_DIR
from pathlib import Path
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field

# --- Exceptions ---
class ContextStaleError(Exception):
    """Raised when the agent's context is invalid due to user intervention."""
    pass


# --- Data Models ---
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
    focus_path: Path = Field(default_factory=lambda: AURIC_WORKSPACE_DIR / "grimoire" / "FOCUS.md", description="Path to the focus file.")
    state: FocusState = Field(default=FocusState.NEW, description="Derived state of the focus.")

    def get_active_step(self) -> Optional[str]:
        """Returns the text of the first incomplete step."""
        for step in self.plan_steps:
            if not step.get("completed", False):
                return step.get("step")
        return None


# --- Focus Manager ---
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

    def __init__(self, focus_file_path: Path):
        self._focus_file_path = focus_file_path
        self._is_stale = False
        self._lock = threading.Lock()

    # --- Interruption Logic ---

    def notify_user_edit(self):
        """Signal that the user has modified the focus file manually."""
        with self._lock:
            self._is_stale = True

    def check_for_interrupt(self):
        """
        Checks if the context is stale. If so, raises ContextStaleError to restart reasoning.
        """
        with self._lock:
            if self._is_stale:
                self._is_stale = False  # Reset flag after catching
                raise ContextStaleError("Focus file was modified by user. Restarting context.")

    # --- File Operations ---

    def clear(self):
        """Resets the focus file to the default idle state."""
        self._write_to_file(self.DEFAULT_TEMPLATE)

    def load(self) -> FocusModel:
        """
        Parses the FOCUS.md file into a structured model.
        Robust to missing sections.
        """
        content = self._read_from_file()
        return self._parse_markdown(content)

    def update_plan(self, new_plan: FocusModel):
        """
        Serializes the model to Markdown and overwrites FOCUS.md.
        """
        markdown_content = self._serialize_model(new_plan)
        self._write_to_file(markdown_content)

    # --- Private Helpers ---

    def _read_from_file(self) -> str:
        if not self._focus_file_path.exists():
            return ""
        return self._focus_file_path.read_text(encoding="utf-8")

    def _write_to_file(self, content: str):
        # Ensure parent directory exists
        self._focus_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._focus_file_path.write_text(content, encoding="utf-8")

    def _parse_markdown(self, content: str) -> FocusModel:
        """
        Parses raw markdown content using Regex.
        """
        # Regex patterns for sections
        prime_directive_pattern = r"## 🎯 Prime Directive.*?\n(.*?)(?=\n##|\Z)"
        plan_pattern = r"## 📋 Plan of Action.*?\n(.*?)(?=\n##|\Z)"
        memory_pattern = r"## 🧠 Working Memory.*?\n(.*?)(?=\n##|\Z)"

        # Extract sections (dotall to capture newlines)
        prime_match = re.search(prime_directive_pattern, content, re.DOTALL | re.IGNORECASE)
        plan_match = re.search(plan_pattern, content, re.DOTALL | re.IGNORECASE)
        memory_match = re.search(memory_pattern, content, re.DOTALL | re.IGNORECASE)

        prime_directive = prime_match.group(1).strip() if prime_match else ""
        raw_plan = plan_match.group(1).strip() if plan_match else ""
        working_memory = memory_match.group(1).strip() if memory_match else ""

        # Parse Plan Steps
        steps = []
        # Matches "- [x] Step text" or "- [ ] Step text"
        step_pattern = r"-\s*\[([ xX])\]\s*(.*)"
        for line in raw_plan.split('\n'):
            line = line.strip()
            match = re.match(step_pattern, line)
            if match:
                is_checked = match.group(1).lower() == 'x'
                text = match.group(2).strip()
                steps.append({"step": text, "completed": is_checked})

        # Derive State
        if not steps:
            state = FocusState.NEW
        elif all(s['completed'] for s in steps):
            state = FocusState.COMPLETE
        elif not any(s['completed'] for s in steps):
            state = FocusState.NEW
        else:
            state = FocusState.IN_PROGRESS

        return FocusModel(
            prime_directive=prime_directive,
            plan_steps=steps,
            working_memory=working_memory,
            state=state
        )

    def _serialize_model(self, model: FocusModel) -> str:
        """
        Converts FocusModel back to the specific Markdown format.
        """
        lines = ["# 🔮 THE FOCUS (Current State)", ""]

        lines.append("## 🎯 Prime Directive (The \"Why\")")
        lines.append(model.prime_directive if model.prime_directive else "(No directive set)")
        lines.append("")

        lines.append("## 📋 Plan of Action (The \"How\")")
        if not model.plan_steps:
            lines.append("- [ ] (No steps defined)")
        else:
            for step in model.plan_steps:
                mark = "x" if step['completed'] else " "
                lines.append(f"- [{mark}] {step['step']}")
        lines.append("")

        lines.append("## 🧠 Working Memory (Scratchpad)")
        lines.append(model.working_memory if model.working_memory else "(Empty)")
        lines.append("")

        return "\n".join(lines)
