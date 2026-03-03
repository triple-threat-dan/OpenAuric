"""
SessionRouter: Maps external contexts to internal session IDs.

Responsible for ensuring that messages from the same platform source 
(e.g., a specific Discord channel) are consistently routed to the same 
chat session, while allowing for explicit session rotation and closure.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from uuid import uuid4

from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.core.session_router")

class SessionRouter:
    """
    Manages context-to-session mapping and persists it to disk.
    
    A 'context' is a platform-specific identifier (e.g., "discord:12345").
    A 'session_id' is a unique UUID used for the chat history.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or (AURIC_ROOT / "active_sessions.json")
        self.active_sessions: Dict[str, str] = {}
        self._closed_contexts: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """Loads session mapping from disk, supporting legacy and modern formats."""
        if not self.storage_path.exists():
            return

        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if isinstance(data, dict):
                # Modern format: {"active_sessions": {...}, "closed_contexts": [...]}
                if "active_sessions" in data:
                    self.active_sessions = data.get("active_sessions", {})
                    self._closed_contexts = set(data.get("closed_contexts", []))
                else:
                    # Legacy format: {...} (direct context-to-session map)
                    self.active_sessions = data
            else:
                logger.warning(f"Unexpected data format in {self.storage_path}. Starting fresh.")
        except Exception as e:
            logger.error(f"Failed to load active sessions from {self.storage_path}: {e}")

    def _save(self) -> None:
        """Persists the current state to disk."""
        try:
            data = {
                "active_sessions": self.active_sessions,
                "closed_contexts": list(self._closed_contexts)
            }
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save active sessions to {self.storage_path}: {e}")

    def get_active_session_id(self, context: str) -> Optional[str]:
        """
        Retrieves the current session ID for a context.
        
        If the context is closed, returns None.
        If no session exists, automatically creates one.
        """
        if context in self._closed_contexts:
            logger.info(f"Context '{context}' is explicitly closed.")
            return None
        
        if context not in self.active_sessions:
            new_sid = str(uuid4())
            self.active_sessions[context] = new_sid
            logger.info(f"Auto-created session {new_sid} for context '{context}'")
            self._save()
        
        return self.active_sessions[context]

    def start_new_session(self, context: str) -> str:
        """Forces a fresh session for the context, clearing any closed state."""
        old_sid = self.active_sessions.get(context)
        new_sid = str(uuid4())
        
        self.active_sessions[context] = new_sid
        self._closed_contexts.discard(context)
        
        log_msg = f"Rotated session {old_sid} -> {new_sid}" if old_sid else f"Started new session {new_sid}"
        logger.info(f"Context '{context}': {log_msg}")
            
        self._save()
        return new_sid

    def close_session(self, context: str) -> Optional[str]:
        """Closes the active session and marks the context as blocked."""
        session_id = self.active_sessions.pop(context, None)
        
        if session_id:
            self._closed_contexts.add(context)
            self._save()
            logger.info(f"Closed session {session_id} for context '{context}' (blocked)")
        else:
            logger.warning(f"No active session found to close for context '{context}'")
        
        return session_id

    def close_all_sessions(self) -> List[Tuple[str, str]]:
        """Closes all active sessions and blocks all their contexts."""
        closed_pairs = list(self.active_sessions.items())
        
        self._closed_contexts.update(self.active_sessions.keys())
        self.active_sessions.clear()
        
        self._save()
        logger.info(f"Closed all {len(closed_pairs)} active sessions.")
        
        return closed_pairs

    def is_context_closed(self, context: str) -> bool:
        """Checks if a context is currently marked as closed."""
        return context in self._closed_contexts

    def list_active_contexts(self) -> Dict[str, str]:
        """Returns a snapshot of the current context-to-session mapping."""
        return self.active_sessions.copy()

    def get_all_active_session_ids(self) -> Set[str]:
        """Returns a set of all session IDs currently in use."""
        return set(self.active_sessions.values())
