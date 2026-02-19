import logging
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from auric.core.config import AuricConfig, AURIC_ROOT

class SystemLogger:
    """
    Handles system-wide JSONL logging with rotation.
    Singleton-ish access pattern via class method.
    """
    _instance = None

    def __init__(self, config: AuricConfig):
        self.config = config
        self.logger = logging.getLogger("auric.system")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False # Do not propagate to root logger (console)

        # Ensure we don't add multiple handlers if re-initialized
        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        if not config.agents.defaults.logging.enabled:
            self.logger.addHandler(logging.NullHandler())
            return

        # Setup File Handler
        log_dir_str = config.agents.defaults.logging.log_dir
        # If relative, make it relative to AURIC_ROOT for consistency, or CWD?
        # Config says ".auric/logs", so likely relative to CWD.
        # But let's verify if user meant relative to .auric root or workspace.
        # Default is ".auric/logs".
        
        # We will treat it as relative to CWD.
        log_dir = Path(log_dir_str)
        if not log_dir.is_absolute():
            log_dir = Path.cwd() / log_dir
            
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)
            
        log_file = log_dir / "system.jsonl"
        
        max_bytes = config.agents.defaults.logging.max_size_mb * 1024 * 1024
        backup_count = config.agents.defaults.logging.backup_count
        
        handler = RotatingFileHandler(
            log_file, 
            maxBytes=max_bytes, 
            backupCount=backup_count, 
            encoding='utf-8'
        )
        
        formatter = JSONLFormatter()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    @classmethod
    def get_instance(cls, config: Optional[AuricConfig] = None) -> 'SystemLogger':
        if cls._instance is None:
            if config is None:
                # Late import to verify config is loaded or raise error
                from auric.core.config import load_config
                config = load_config()
            cls._instance = cls(config)
        return cls._instance

    def log(self, event_type: str, data: Dict[str, Any], session_id: Optional[str] = None, level: str = "INFO"):
        """
        Logs a structured event.
        
        Args:
            event_type: A distinct category for the event (e.g., 'TOOL_CALL', 'LLM_RESPONSE').
            data: Key-value data payload.
            session_id: The active session ID, if any.
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
        
        # We log strictly the message as JSON. 
        # The Formatter will ensure it's written correctly, but here we pass the dict 
        # so the formatter can handle it, or we dump it here.
        # Standard logging expects a string message.
        # Let's dump it here to ensure it's valid JSON line.
        # Actually, let's use the `extra` dict or just pass the dict as msg and have formatter handle it?
        # The simplest reliability is to dump here.
        
        # Check level
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(payload)


class JSONLFormatter(logging.Formatter):
    """
    Format standard logging records as JSONL.
    Expects `msg` to be a dict or string.
    """
    def format(self, record):
        if isinstance(record.msg, dict):
            # It's already our structured payload
            return json.dumps(record.msg, default=str)
        else:
            # It's a legacy string log or something else
            return json.dumps({
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "event": "SYSTEM_MSG",
                "level": record.levelname,
                "data": {"message": str(record.msg)}
            }, default=str)
