import json
import logging
from pathlib import Path
from typing import Dict, Optional, List, Set, Tuple
from uuid import uuid4

from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.core.session_router")

class SessionRouter:
    """
    Manages the mapping between 'Contexts' (e.g., a Discord Channel ID, a specific User)
    and 'Session IDs' (the UUID used for the chat log).

    Persists this mapping to `.auric/active_sessions.json`.
    
    Tracks explicitly closed contexts to prevent zombie session reactivation.
    Once a context is closed, `get_active_session_id()` returns None instead of
    auto-creating a new session. Use `start_new_session()` to explicitly create
    a fresh session for a closed context.
    """
    def __init__(self, storage_path: Path = None):
        if storage_path:
            self.storage_path = storage_path
        else:
            self.storage_path = AURIC_ROOT / "active_sessions.json"
        
        self.active_sessions: Dict[str, str] = {}
        self._closed_contexts: Set[str] = set()
        self._load()

    def _load(self):
        """Load active sessions from disk."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Support both old format (plain dict) and new format (dict with metadata)
                if isinstance(data, dict) and "active_sessions" in data:
                    self.active_sessions = data.get("active_sessions", {})
                    self._closed_contexts = set(data.get("closed_contexts", []))
                elif isinstance(data, dict):
                    # Legacy format: plain dict of context -> session_id
                    self.active_sessions = data
                    self._closed_contexts = set()
                else:
                    self.active_sessions = {}
                    self._closed_contexts = set()
                    
            except Exception as e:
                logger.error(f"Failed to load active sessions: {e}. Starting fresh.")
                self.active_sessions = {}
                self._closed_contexts = set()
        else:
            self.active_sessions = {}
            self._closed_contexts = set()

    def _save(self):
        """Save active sessions to disk."""
        try:
            data = {
                "active_sessions": self.active_sessions,
                "closed_contexts": list(self._closed_contexts)
            }
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save active sessions: {e}")

    def get_active_session_id(self, context: str) -> Optional[str]:
        """
        Get the active session ID for a given context.
        
        Returns None if the context was explicitly closed — callers should
        use `start_new_session()` to create a fresh session in that case.
        
        If no session exists and the context is NOT closed, creates a new one
        (first-contact auto-creation).
        """
        # If this context was explicitly closed, do NOT auto-create
        if context in self._closed_contexts:
            logger.info(f"Context '{context}' is closed. Returning None (caller should create new session).")
            return None
        
        if context not in self.active_sessions:
            new_sid = str(uuid4())
            self.active_sessions[context] = new_sid
            logger.info(f"Created new session {new_sid} for context '{context}'")
            self._save()
        
        return self.active_sessions[context]

    def start_new_session(self, context: str) -> str:
        """
        Forces a NEW session ID for the given context.
        Also clears the closed state if the context was previously closed.
        Returns the new session ID.
        """
        old_sid = self.active_sessions.get(context)
        new_sid = str(uuid4())
        self.active_sessions[context] = new_sid
        
        # Clear closed state — this is an explicit "start fresh"
        self._closed_contexts.discard(context)
        
        if old_sid:
            logger.info(f"Context '{context}': Rotated session {old_sid} -> {new_sid}")
        else:
            logger.info(f"Context '{context}': Started new session {new_sid}")
            
        self._save()
        return new_sid

    def close_session(self, context: str) -> Optional[str]:
        """
        Closes the active session for a context and marks the context as closed.
        
        Returns the session_id that was closed (for summarization), or None if 
        no active session existed.
        
        Next time get_active_session_id is called for this context, it will
        return None. Use start_new_session() to explicitly create a new one.
        """
        old_sid = self.active_sessions.pop(context, None)
        
        if old_sid:
            self._closed_contexts.add(context)
            self._save()
            logger.info(f"Closed session {old_sid} for context '{context}' (context now blocked)")
        else:
            logger.warning(f"No active session to close for context '{context}'")
        
        return old_sid

    def close_all_sessions(self) -> List[Tuple[str, str]]:
        """
        Clears ALL active sessions and marks all contexts as closed.
        
        Returns a list of (context, session_id) tuples for each session that
        was closed, so callers can trigger summarization for each.
        """
        closed_pairs = list(self.active_sessions.items())
        
        # Mark all contexts as closed
        for context in self.active_sessions:
            self._closed_contexts.add(context)
        
        self.active_sessions = {}
        self._save()
        logger.info(f"Closed all {len(closed_pairs)} active sessions.")
        
        return closed_pairs

    def is_context_closed(self, context: str) -> bool:
        """Check if a context has been explicitly closed."""
        return context in self._closed_contexts

    def list_active_contexts(self) -> Dict[str, str]:
        """
        Returns a copy of the active sessions map.
        """
        return self.active_sessions.copy()

    def get_all_active_session_ids(self) -> Set[str]:
        """
        Returns the set of all currently active session IDs.
        Useful for cross-referencing with DB session lists.
        """
        return set(self.active_sessions.values())
