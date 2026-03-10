"""
PairingManager: User authentication and device pairing for OpenAuric.

Handles dynamic authorization for platform adapters (Pacts), allowing 
users to pair their accounts via shortcodes and persist permissions 
to disk.
"""

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.core.pairing")

class PairingManager:
    """
    Manages user pairing and authorization for Pacts.
    Stores credentials in .auric/credentials/
    """
    _instance: Optional['PairingManager'] = None

    def __init__(self):
        self.creds_dir = AURIC_ROOT / "credentials"
        self.creds_dir.mkdir(parents=True, exist_ok=True)
        # In-memory cache for frequently accessed allowed users
        self._allow_cache: Dict[str, Dict[str, str]] = {}

    @classmethod
    def get_instance(cls) -> 'PairingManager':
        """Singleton accessor."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_pairing_file(self, pact: str) -> Path:
        return self.creds_dir / f"{pact}-pairing.json"

    def _get_allow_file(self, pact: str) -> Path:
        return self.creds_dir / f"{pact}-allowFrom.json"

    def _load_json(self, path: Path) -> Dict[str, Any]:
        """Loads and returns JSON data from the specified path."""
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return {}

    def _save_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Persists the data dictionary to disk as JSON."""
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")

    def is_user_allowed(self, pact: str, user_id: Any, config_allowed: Optional[List[str]] = None) -> bool:
        """
        Check if a user is allowed via config OR pairing file.
        """
        user_id_str = str(user_id)
        
        # 1. Check legacy/static config
        if config_allowed and user_id_str in config_allowed:
            return True
        
        # 2. Check dynamic pairing file (via cache)
        if pact not in self._allow_cache:
            allow_file = self._get_allow_file(pact)
            self._allow_cache[pact] = self._load_json(allow_file)
            
        return user_id_str in self._allow_cache[pact]

    def create_request(self, pact: str, user_id: Any, user_name: str) -> str:
        """
        Creates a pairing request for a user. Returns the shortcode.
        If a request already exists, returns the existing code.
        """
        pairing_file = self._get_pairing_file(pact)
        pending = self._load_json(pairing_file)
        
        user_id_str = str(user_id)
        
        # Deduplication check
        for code, data in pending.items():
            if data.get("user_id") == user_id_str:
                return str(code)

        # Generate new shortcode (6 chars, uppercase)
        code = secrets.token_hex(3).upper() 
        
        pending[code] = {
            "user_id": user_id_str,
            "user_name": user_name,
            "timestamp": datetime.now().isoformat()
        }
        self._save_json(pairing_file, pending)
        
        logger.info(f"New Pairing Request: {user_name} ({user_id_str}) -> Code: {code}")
        return code

    def list_requests(self, pact: str) -> Dict[str, Dict[str, Any]]:
        """List pending pairing requests for a pact."""
        return self._load_json(self._get_pairing_file(pact))

    def approve_request(self, pact: str, shortcode: str) -> Optional[str]:
        """
        Approve a pairing request. Moves user to allowFrom.json.
        Returns the username of the approved user, or None if not found.
        """
        pairing_file = self._get_pairing_file(pact)
        pending = self._load_json(pairing_file)
        
        # Case insensitive lookup
        target_code = next((c for c in pending if c.upper() == shortcode.upper()), None)
        
        if not target_code:
            return None
            
        request = pending.pop(target_code)
        self._save_json(pairing_file, pending)
        
        # Add to allowed list and sync cache
        allow_file = self._get_allow_file(pact)
        allowed = self._load_json(allow_file)
        allowed[request["user_id"]] = request["user_name"]
        
        self._save_json(allow_file, allowed)
        self._allow_cache[pact] = allowed # Update cache
        
        logger.info(f"Approved pairing for {request['user_name']} ({request['user_id']})")
        return str(request["user_name"])
