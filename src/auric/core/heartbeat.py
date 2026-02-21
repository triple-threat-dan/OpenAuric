"""
HeartbeatManager: The Pulse of OpenAuric.

Tracks user activity to determine "idle" states and manages the triggering
of the "Dream Cycle" (maintenance/summarization) and "Vigil" (scheduled checks).
"""

import os
import logging
import asyncio
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from auric.core.config import AURIC_WORKSPACE_DIR, AURIC_ROOT, load_config
from auric.memory import chronicles
from auric.core.database import AuditLogger

logger = logging.getLogger("auric.core.heartbeat")

class HeartbeatManager:
    """
    Tracks the last time the user interacted with the system.
    Determines if the agent is 'idle' enough to dream.
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
        """
        Updates the last active timestamp to now.
        Call this whenever the user sends a message or interacts via CLI.
        """
        self._last_active_timestamp = datetime.now()
        # logger.debug(f"Heartbeat: Activity detected. idle_timer reset at {self._last_active_timestamp}")
        # Could log activity beat here, but might be too noisy. Let's stick to vigil/dream beats.

    def is_idle(self, threshold_minutes: int = 30) -> bool:
        """
        Returns True if no activity has been detected for `threshold_minutes`.
        """
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
    
    # Condition 1: Collision Avoidance
    # We use a hardcoded 30m for now, or could pull from config if added later.
    if not hb.is_idle(threshold_minutes=30):
        logger.info("Skipping Dream Cycle: User is active.")
        return False

    # Condition 2: Data Availability
    # Check ./.auric/logs/current_session.log (assuming standard path)
    # We can refine this path later if config changes.
    log_path = AURIC_ROOT / "logs" / "current_session.log"
    
    if not log_path.exists():
        logger.debug("Skipping Dream Cycle: No session log found.")
        return False
        
    if log_path.stat().st_size == 0:
        logger.debug("Skipping Dream Cycle: Session log is empty.")
        return False
        
    # Condition 3 (Optional): Check if we already dreamt recently.
    # skipped for this iteration to keep it simple.
    
    return True

async def run_dream_cycle_task():
    """
    APScheduler task wrapper for the Dream Cycle.
    """
    logger.debug("Heartbeat: Checking Dream Cycle conditions...")
    
    if can_dream():
        logger.info("Heartbeat: Conditions met. Entering Dream Cycle... 💤")
        try:
            await chronicles.perform_dream_cycle()
            logger.info("Heartbeat: Woke up from Dream Cycle.")
        except Exception as e:
            logger.error(f"Heartbeat: Nightmare detected (Dream Cycle failed): {e}")
    else:
        # It's noisy to log this every time if it runs frequently, 
        # but 'debug' is fine.
        pass

# ==============================================================================
# Vigil Logic
# ==============================================================================

async def run_heartbeat_task(command_bus: Optional[asyncio.Queue] = None):
    """
    APScheduler task for 'Heartbeat' checks (formerly Vigil).
    
    Checks if active hours are valid.
    If valid and HEARTBEAT.md exists, injects a system message into the command_bus
    to wake up the agent.
    """
    print(f"💓 Heartbeat Triggered: {datetime.now().strftime('%H:%M:%S')}")
    logger.info("Heartbeat: Pulse triggered.")

    # Log persistent heartbeat
    hb = HeartbeatManager.get_instance()
    
    # Generate unique session ID for this specific heartbeat
    # This prevents history pollution
    from uuid import uuid4
    session_id = f"heartbeat-{uuid4()}"

    # Check Active Hours Logic first to determine status
    status = "ALIVE"
    config = load_config()
    active_window = config.agents.defaults.heartbeat.active_hours
    
    # Simple active hours check 'HH:MM-HH:MM'
    in_hours = False
    try:
        start_str, end_str = active_window.split('-')
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        start_h, start_m = map(int, start_str.split(':'))
        end_h, end_m = map(int, end_str.split(':'))
        
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        
        start_ts = datetime.combine(now.date(), datetime.min.time()) + timedelta(minutes=start_minutes)
        end_ts = datetime.combine(now.date(), datetime.min.time()) + timedelta(minutes=end_minutes)
        
        # Handle crossing midnight
        if end_minutes < start_minutes:
            end_ts += timedelta(days=1)
            # Logic for crossing midnight... simplified check:
            if start_minutes <= current_minutes or current_minutes <= end_minutes:
                in_hours = True
            else:
                in_hours = False
        else:
             in_hours = start_minutes <= current_minutes <= end_minutes
            
    except Exception as e:
         logger.warning(f"Heartbeat: Could not parse active hours '{active_window}': {e}. Proceeding anyway.")
         in_hours = True # Fail open? Or closed? Let's say open for safety.

    if not in_hours:
        status = "SKIPPED"
        print(f"💤 Heartbeat Skipped: Outside active hours ({active_window})")
        logger.info(f"Heartbeat: Outside active hours ({active_window}). Pulse skipped.")

    # Always log the heartbeat attempt
    heartbeat_file = AURIC_ROOT / "HEARTBEAT.md"
    if hb.audit_logger:
        await hb.audit_logger.log_heartbeat(status=status, meta={
            "active_window": active_window, 
            "in_hours": in_hours,
            "has_heartbeat_file": heartbeat_file.exists()
        })

    if status == "SKIPPED":
        return

    # Check Heartbeat File
    if heartbeat_file.exists():
        content = heartbeat_file.read_text(encoding="utf-8")
        
        # Check for actual task lines: starts with dash, then potentially space and open bracket (for [ ], [x], etc.)
        # but NOT just headers or text.
        import re
        has_pending = bool(re.search(r"^\s*-\s+.*", content, re.MULTILINE))

        if has_pending:
            logger.info("Heartbeat: Pending tasks detected in HEARTBEAT.md. Waking agent...")
            if command_bus:
                # Provide absolute path to help agent find the file
                heartbeat_path_str = str(heartbeat_file.resolve())
                
                prompt = (
                    "🔴 **SYSTEM HEARTBEAT TRIGGERED**\n\n"
                    f"The system heartbeat has activated. Please review your `HEARTBEAT.md` checklist below (located at `{heartbeat_path_str}`) and perform any pending tasks.\n\n"
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
                    "   - If tasks ARE actionable, output the JSON tool calls (e.g., `web-search`, `discord_send_channel_message`, `write_file`) to execute them.\n"
                    "3. **DO NOT** chat, say 'Okay', or use your persona outside of the thinking block. The only text outside the thinking block must be `<|stop|>` or valid tool calls.\n"
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
                logger.warning("Heartbeat: No command_bus connection!")
        else:
            logger.debug("Heartbeat: No pending tasks found in HEARTBEAT.md.")
    else:
        logger.debug("Heartbeat: No HEARTBEAT.md found.")
