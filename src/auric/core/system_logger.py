"""
System-wide structured logging for OpenAuric.

Handles JSONL logging with rotation, ensuring all system events are
captured in a machine-readable format for audit and debugging.
"""

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from auric.core.config import AuricConfig

class SystemLogger:
    """
    Handles system-wide JSONL logging with rotation.
    Ensures structured events are captured consistently across the engine.
    """
    _instance: Optional['SystemLogger'] = None
    _LEVEL_MAP = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }

    def __init__(self, config: AuricConfig):
        self.config = config
        self.logger = logging.getLogger("auric.system")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        log_config = config.agents.defaults.logging
        if not log_config.enabled:
            self.logger.addHandler(logging.NullHandler())
            return

        log_dir = Path(log_config.log_dir)
        if not log_dir.is_absolute():
            log_dir = Path.cwd() / log_dir
            
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "system.jsonl"
        
        max_bytes = log_config.max_size_mb * 1024 * 1024
        
        handler = RotatingFileHandler(
            log_file, 
            maxBytes=max_bytes, 
            backupCount=log_config.backup_count, 
            encoding='utf-8'
        )
        handler.setFormatter(JSONLFormatter())
        self.logger.addHandler(handler)

    @classmethod
    def get_instance(cls, config: Optional[AuricConfig] = None) -> 'SystemLogger':
        """Singleton accessor."""
        if cls._instance is None:
            if config is None:
                from auric.core.config import load_config
                config = load_config()
            cls._instance = cls(config)
        return cls._instance

    def log(self, event_type: str, data: Dict[str, Any], session_id: Optional[str] = None, level: str = "INFO") -> None:
        """
        Logs a structured event in JSONL format.
        
        Args:
            event_type: Category for the event (e.g., 'TOOL_CALL', 'LLM_RESPONSE').
            data: Payload data.
            session_id: The active session ID.
            level: Log level (INFO, WARNING, ERROR).
        """
        if not self.config.agents.defaults.logging.enabled:
            return

        payload = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "session_id": session_id,
            "level": level,
            "data": data
        }
        
        log_level = self._LEVEL_MAP.get(level.upper(), logging.INFO)
        self.logger.log(log_level, payload)


class JSONLFormatter(logging.Formatter):
    """Formats standard and structured logging records as JSONL."""
    
    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            # Enforce timestamp from construction time if constructed by SystemLogger.log
            # or from record creation time if injected elsewhere.
            payload = record.msg
            if "timestamp" not in payload:
                payload["timestamp"] = datetime.fromtimestamp(record.created).isoformat()
            return json.dumps(payload, default=str)
        
        # Fallback for standard string logs
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "event": "SYSTEM_MSG",
            "level": record.levelname,
            "data": {"message": str(record.msg)}
        }, default=str)
