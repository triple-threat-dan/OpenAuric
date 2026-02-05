"""
HeartbeatManager: The Pulse of OpenAuric.

Tracks user activity to determine "idle" states and manages the triggering
of the "Dream Cycle" (maintenance/summarization) and "Vigil" (scheduled checks).
"""

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from auric.core.config import load_config
from auric.memory import chronicles

logger = logging.getLogger("auric.core.heartbeat")

class HeartbeatManager:
    """
    Tracks the last time the user interacted with the system.
    Determines if the agent is 'idle' enough to dream.
    """
    
    _instance: Optional['HeartbeatManager'] = None

    def __init__(self):
        self._last_active_timestamp: datetime = datetime.now()
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
        logger.debug(f"Heartbeat: Activity detected. idle_timer reset at {self._last_active_timestamp}")

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
    # Check ~/.auric/logs/current_session.log (assuming standard path)
    # We can refine this path later if config changes.
    log_path = Path.home() / ".auric" / "logs" / "current_session.log"
    
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

async def run_vigil_task():
    """
    APScheduler task for 'Vigil' checks.
    
    Checks if active hours are valid and if heartbeat file exists.
    """
    config = load_config()
    active_window = config.agents.defaults.heartbeat.active_hours
    
    # Simple active hours check 'HH:MM-HH:MM'
    # For now we just parse it simply; a robust implementation might need dateutil or similar
    # but let's stick to simple int comparison for simplicity unless library is available.
    
    try:
        start_str, end_str = active_window.split('-')
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        start_h, start_m = map(int, start_str.split(':'))
        end_h, end_m = map(int, end_str.split(':'))
        
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        
        in_hours = False
        if start_minutes <= end_minutes:
            in_hours = start_minutes <= current_minutes <= end_minutes
        else: # Crosses midnight
            in_hours = current_minutes >= start_minutes or current_minutes <= end_minutes
            
        if not in_hours:
            logger.debug(f"Heartbeat: Outside active hours ({active_window}). Vigil rests.")
            return

    except Exception as e:
         logger.warning(f"Heartbeat: Could not parse active hours '{active_window}': {e}. Proceeding anyway.")

    # Check Heartbeat File
    heartbeat_file = Path.home() / ".auric" / "HEARTBEAT.md"
    if heartbeat_file.exists():
         logger.info("Heartbeat: Checking vigil... (HEARTBEAT.md detected)")
         # Placeholder for actual vigil logic (Epic 5)
    else:
        logger.debug("Heartbeat: No HEARTBEAT.md found. Vigil holds.")
