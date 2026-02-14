import json
import logging
import secrets
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from auric.core.config import AURIC_ROOT

logger = logging.getLogger("auric.core.pairing")

class PairingManager:
    """
    Manages user pairing and authorization for Pacts.
    Stores credentials in .auric/credentials/
    """
    def __init__(self):
        self.creds_dir = AURIC_ROOT / "credentials"
        self.creds_dir.mkdir(parents=True, exist_ok=True)

    def _get_pairing_file(self, pact: str) -> Path:
        return self.creds_dir / f"{pact}-pairing.json"

    def _get_allow_file(self, pact: str) -> Path:
        return self.creds_dir / f"{pact}-allowFrom.json"

    def _load_json(self, path: Path) -> Dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return {}

    def _save_json(self, path: Path, data: Dict) -> None:
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")

    def is_user_allowed(self, pact: str, user_id: str, config_allowed: List[str] = []) -> bool:
        """
        Check if a user is allowed via config OR pairing file.
        """
        # 1. Check legacy/static config
        if user_id in config_allowed:
            return True
        
        # 2. Check dynamic pairing file
        allow_file = self._get_allow_file(pact)
        allowed_data = self._load_json(allow_file)
        return str(user_id) in allowed_data

    def create_request(self, pact: str, user_id: str, user_name: str) -> str:
        """
        Creates a pairing request for a user. Returns the shortcode.
        If a request already exists, returns the existing code.
        """
        pairing_file = self._get_pairing_file(pact)
        pending = self._load_json(pairing_file)
        
        user_id_str = str(user_id)
        
        # Check if already pending (dedup by user_id)
        for code, data in pending.items():
            if data["user_id"] == user_id_str:
                return code

        # Generate new shortcode (6 chars, uppercase)
        code = secrets.token_hex(3).upper() 
        
        pending[code] = {
            "user_id": user_id_str,
            "user_name": user_name,
            "timestamp": datetime.now().isoformat()
        }
        self._save_json(pairing_file, pending)
        logger.info(f"Created pairing request {code} for {user_name} ({user_id})")
        
        # Print to console for visibility
        print(f"[PAIRING] New Request from {user_name} ({user_id}). Code: {code}")
        
        return code

    def list_requests(self, pact: str) -> Dict[str, Dict]:
        """
        List pending pairing requests.
        """
        return self._load_json(self._get_pairing_file(pact))

    def approve_request(self, pact: str, shortcode: str) -> Optional[str]:
        """
        Approve a pairing request. Moves user to allowFrom.json.
        Returns the username of the approved user, or None if not found.
        """
        pairing_file = self._get_pairing_file(pact)
        pending = self._load_json(pairing_file)
        
        # Case insensitive lookup
        target_code = None
        for code in pending.keys():
            if code.upper() == shortcode.upper():
                target_code = code
                break
        
        if not target_code:
            return None
            
        request = pending.pop(target_code)
        self._save_json(pairing_file, pending)
        
        # Add to allowed
        allow_file = self._get_allow_file(pact)
        allowed = self._load_json(allow_file)
        allowed[request["user_id"]] = request["user_name"]
        self._save_json(allow_file, allowed)
        
        logger.info(f"Approved pairing for {request['user_name']} ({request['user_id']})")
        return request["user_name"]
