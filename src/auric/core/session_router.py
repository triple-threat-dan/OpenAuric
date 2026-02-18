import json
import logging
from pathlib import Path
from typing import Dict, Optional, List
from uuid import uuid4

from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.core.session_router")

class SessionRouter:
    """
    Manages the mapping between 'Contexts' (e.g., a Discord Channel ID, a specific User)
    and 'Session IDs' (the UUID used for the chat log).

    Persists this mapping to `.auric/active_sessions.json`.
    """
    def __init__(self, storage_path: Path = None):
        if storage_path:
            self.storage_path = storage_path
        else:
            self.storage_path = AURIC_ROOT / "active_sessions.json"
        
        self.active_sessions: Dict[str, str] = {}
        self._load()

    def _load(self):
        """Load active sessions from disk."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    self.active_sessions = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load active sessions: {e}. Starting fresh.")
                self.active_sessions = {}
        else:
            self.active_sessions = {}

    def _save(self):
        """Save active sessions to disk."""
        try:
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(self.active_sessions, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save active sessions: {e}")

    def get_active_session_id(self, context: str) -> str:
        """
        Get the active session ID for a given context.
        If none exists, creates a new one.
        """
        if context not in self.active_sessions:
            new_sid = str(uuid4())
            self.active_sessions[context] = new_sid
            logger.info(f"Created new session {new_sid} for context '{context}'")
            self._save()
        
        return self.active_sessions[context]

    def start_new_session(self, context: str) -> str:
        """
        Forces a NEW session ID for the given context.
        Returns the new session ID.
        """
        old_sid = self.active_sessions.get(context)
        new_sid = str(uuid4())
        self.active_sessions[context] = new_sid
        
        if old_sid:
            logger.info(f"Context '{context}': Rotated session {old_sid} -> {new_sid}")
        else:
            logger.info(f"Context '{context}': Started new session {new_sid}")
            
        self._save()
        return new_sid

    def close_session(self, context: str):
        """
        Removes the active session for a context. 
        Next time get_active_session_id is called, a new one will be generated.
        """
        if context in self.active_sessions:
            del self.active_sessions[context]
            self._save()
            logger.info(f"Closed session for context '{context}'")

    def close_all_sessions(self):
        """
        Clears ALL active sessions. Nuclear option.
        """
        count = len(self.active_sessions)
        self.active_sessions = {}
        self._save()
        logger.info(f"Closed all {count} active sessions.")

    def list_active_contexts(self) -> Dict[str, str]:
        """
        Returns a copy of the active sessions map.
        """
        return self.active_sessions.copy()
