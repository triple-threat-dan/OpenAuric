"""
HeartbeatManager: The Pulse of OpenAuric.

Tracks user activity to determine "idle" states and manages the triggering
of the "Dream Cycle" (maintenance/summarization) and "Vigil" (scheduled checks).
"""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

from auric.core.config import AURIC_ROOT, load_config
from auric.core.database import AuditLogger
from auric.memory import chronicles

logger = logging.getLogger("auric.core.heartbeat")

# Pre-compiled regex for performance
PENDING_TASK_RE = re.compile(r"^\s*-\s+.*", re.MULTILINE)

class HeartbeatManager:
    """
    Tracks user activity to determine if the agent is 'idle'.
    """
    _instance: Optional['HeartbeatManager'] = None

    def __init__(self, audit_logger: Optional[AuditLogger] = None):
        self._last_active_timestamp: datetime = datetime.now()
        self.audit_logger = audit_logger
        logger.debug(f"HeartbeatManager initialized at {self._last_active_timestamp}")

    @classmethod
    def get_instance(cls) -> 'HeartbeatManager':
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def touch(self) -> None:
        """Updates the last active timestamp to now."""
        self._last_active_timestamp = datetime.now()

    def is_idle(self, threshold_minutes: int = 30) -> bool:
        """Returns True if no activity has been detected for the threshold."""
        delta = datetime.now() - self._last_active_timestamp
        is_idle = delta > timedelta(minutes=threshold_minutes)
        if is_idle:
            logger.debug(f"Heartbeat: System is idle (inactive for {delta}).")
        return is_idle

    @property
    def last_active(self) -> datetime:
        return self._last_active_timestamp


# ==============================================================================
# Dream Cycle Logic
# ==============================================================================

def can_dream() -> bool:
    """
    Determines if conditions are right to enter the Dream Cycle.
    
    Conditions:
    1. User is idle (Collision Avoidance).
    2. Data is available (Session log has content).
    """
    hb = HeartbeatManager.get_instance()
    
    if not hb.is_idle(threshold_minutes=30):
        logger.info("Skipping Dream Cycle: User is active.")
        return False

    log_path = AURIC_ROOT / "logs" / "current_session.log"
    
    if not log_path.exists() or log_path.stat().st_size == 0:
        logger.debug("Skipping Dream Cycle: No session log found or log is empty.")
        return False
        
    return True

async def run_dream_cycle_task():
    """APScheduler task wrapper for the Dream Cycle."""
    logger.debug("Heartbeat: Checking Dream Cycle conditions...")
    
    if can_dream():
        logger.info("Heartbeat: Conditions met. Entering Dream Cycle... 💤")
        try:
            await chronicles.perform_dream_cycle()
            logger.info("Heartbeat: Woke up from Dream Cycle.")
        except Exception as e:
            logger.error(f"Heartbeat: Nightmare detected (Dream Cycle failed): {e}")


# ==============================================================================
# Vigil Logic
# ==============================================================================

def is_within_active_hours(window_str: str) -> bool:
    """Checks if the current time falls within the HH:MM-HH:MM window."""
    try:
        start_str, end_str = window_str.split('-')
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        sh, sm = map(int, start_str.split(':'))
        eh, em = map(int, end_str.split(':'))
        
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        
        if end_minutes < start_minutes:  # Crosses midnight
            return current_minutes >= start_minutes or current_minutes <= end_minutes
        
        return start_minutes <= current_minutes <= end_minutes
    except Exception as e:
         logger.warning(f"Heartbeat: Could not parse active hours '{window_str}': {e}")
         return True  # Fail open

async def run_heartbeat_task(command_bus: Optional[asyncio.Queue] = None):
    """
    APScheduler task for periodic pulse checks.
    
    Checks for pending tasks in HEARTBEAT.md during active hours.
    """
    logger.info("Heartbeat: Pulse triggered.")

    hb = HeartbeatManager.get_instance()
    config = load_config()
    active_window = config.agents.defaults.heartbeat.active_hours
    
    in_hours = is_within_active_hours(active_window)
    status = "ALIVE" if in_hours else "SKIPPED"

    if not in_hours:
        logger.info(f"Heartbeat: Outside active hours ({active_window}). Pulse skipped.")

    heartbeat_file = AURIC_ROOT / "HEARTBEAT.md"
    
    if hb.audit_logger:
        await hb.audit_logger.log_heartbeat(status=status, meta={
            "active_window": active_window, 
            "in_hours": in_hours,
            "has_heartbeat_file": heartbeat_file.exists()
        })

    if status == "SKIPPED" or not heartbeat_file.exists():
        return

    content = heartbeat_file.read_text(encoding="utf-8")
    if PENDING_TASK_RE.search(content):
        if not command_bus:
            logger.warning("Heartbeat: No command_bus connection!")
            return

        logger.info("Heartbeat: Pending tasks detected. Waking agent...")
        
        heartbeat_path = str(heartbeat_file.resolve())
        session_id = f"heartbeat-{uuid4()}"
        
        prompt = (
            "🔴 **SYSTEM HEARTBEAT TRIGGERED**\n\n"
            f"The system heartbeat has activated. Please review your `HEARTBEAT.md` checklist below (located at `{heartbeat_path}`) and perform any pending tasks.\n\n"
            "**RULES & STATE TRACKING:**\n"
            "1. **Clean Focus**: You are starting with a clean focus for this heartbeat. Ignore your main background tasks.\n"
            "2. **Recurring Task Tracking**: When you complete a **Recurring Task**, you MUST mutate the `HEARTBEAT.md` file to append a timestamp tag to the end of that specific task line: `[LAST COMPLETED: YYYY-MM-DD]`. Example: `- Between 10am and 11am... [LAST COMPLETED: 2026-02-21]`\n"
            "3. **STRICT TIME CHECK**: Before executing ANY task, you MUST evaluate the time. You cannot do time math in your head. You MUST open a `<thinking>` block and evaluate EVERY task against the Current Time.\n"
            "   - If a task is scheduled for the FUTURE -> SKIP.\n"
            "   - If a task's time window has ALREADY PASSED (e.g., it is 3:00 PM and the task was for 12:00 PM) -> SKIP (do not attempt to 'catch up' on missed recurring tasks).\n"
            "   - If a task is actionable RIGHT NOW -> Mark as ACTIONABLE.\n"
            "4. **One-time Tasks**: After completing a one-time reminder, remove it from the `One-time Reminders` section as usual.\n"
            "5. **No History**: Do NOT track heartbeat task progress in HEARTBEAT.md — use it ONLY for final completion tags.\n\n"
            f"```markdown\n{content}\n```\n\n"
            "**EXECUTION INSTRUCTIONS:**\n"
            "1. You MUST start your response with a `<thinking>...</thinking>` block to evaluate the times.\n"
            "2. Immediately after closing the `</thinking>` tag:\n"
            "   - If NO tasks are actionable right now, output EXACTLY AND ONLY: `<|stop|>`\n"
            "   - If tasks ARE actionable, output the JSON tool calls to execute them.\n"
            "3. **DO NOT** chat or use your persona outside of the thinking block. Only output `<|stop|>` or valid tool calls.\n"
        )
        
        try:
            await command_bus.put({
                "level": "USER",
                "message": prompt,
                "source": "HEARTBEAT",
                "session_id": session_id,
                "heartbeat_source_content": content 
            })
        except Exception as ex:
            logger.error(f"Heartbeat Bus Error: {ex}")
    else:
        logger.debug("Heartbeat: No pending tasks found in HEARTBEAT.md.")
