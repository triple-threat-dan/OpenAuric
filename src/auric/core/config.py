"""
Configuration and Secrets Management for OpenAuric.

This module handles loading configuration from disk (~/.auric/auric.json),
enforcing security permissions, and providing access to secrets.
"""

import os
import sys
import stat
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import json5
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger("auric.config")

# ==============================================================================
# Pydantic Models
# ==============================================================================

class HeartbeatConfig(BaseModel):
    """Configuration for the agent heartbeat mechanism."""
    enabled: bool = True
    interval: str = "30m"
    active_hours: str = Field(default="09:00-18:00", alias="activeHours")
    target: str = "console"

class AgentDefaults(BaseModel):
    """Default settings for agents."""
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)

class AgentsConfig(BaseModel):
    """Configuration for agents."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    smart_model: str = "gemini/gemini-1.5-pro"
    fast_model: str = "gemini/gemini-1.5-flash"
    is_local: bool = False
    max_recursion: int = 2
    max_cost: float = 1.0

class GatewayConfig(BaseModel):
    """Configuration for the API gateway."""
    port: int = 8067
    host: str = "127.0.0.1"

class SandboxConfig(BaseModel):
    """Configuration for the isolated Python sandbox."""
    enabled: bool = True
    allowed_imports: List[str] = Field(default_factory=list)

class TelegramConfig(BaseModel):
    enabled: bool = False
    token: Optional[str] = None

class DiscordConfig(BaseModel):
    enabled: bool = False
    token: Optional[str] = None
    allowed_channels: List[str] = Field(default_factory=list)
    allowed_users: List[str] = Field(default_factory=list)

class PactsConfig(BaseModel):
    """Configuration for platform adapters."""
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)

class LLMKeys(BaseModel):
    """API keys for LLM providers."""
    openai: Optional[str] = None
    anthropic: Optional[str] = None
    gemini: Optional[str] = None
    openrouter: Optional[str] = None

class AuricConfig(BaseSettings):
    """
    Root configuration object for OpenAuric.
    """
    debug: bool = False
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    pacts: PactsConfig = Field(default_factory=PactsConfig)
    keys: LLMKeys = Field(default_factory=LLMKeys) 
    tools: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        strict = True
        populate_by_name = True

# ==============================================================================
# Configuration Loader
# ==============================================================================

class ConfigLoader:
    """
    Responsible for locating, validating, and loading the configuration file.
    Enforces strict file permissions (0600) for security.
    """
    
    DEFAULT_CONFIG_DIR = Path.home() / ".auric"
    CONFIG_FILENAME = "auric.json"

    @classmethod
    def get_config_path(cls) -> Path:
        """Returns the full path to the configuration file."""
        return cls.DEFAULT_CONFIG_DIR / cls.CONFIG_FILENAME

    @classmethod
    def _ensure_permissions(cls, path: Path) -> None:
        """
        Enforce 0600 permissions (Owner Read/Write only).
        On Windows, this attempts to set read-only attributes or ACLs if possible,
        but Python's os.chmod 0o600 checks are mainly effective on Unix-like systems
        or restricted to basic read-only flags on Windows.
        
        However, for a cross-platform tool, we'll implement the standard check
        and log warnings if we detect it's too open on systems that support it.
        """
        # Create directory if it doesn't exist
        if not path.parent.exists():
            try:
                path.parent.mkdir(parents=True, mode=0o700)
            except Exception as e:
                logger.error(f"Failed to create config directory {path.parent}: {e}")
                raise

        # Check file existence
        if not path.exists():
            return  # File will be created later with correct permissions

        # Check and enforce permissions
        try:
            stat_info = path.stat()
            current_mode = stat.S_IMODE(stat_info.st_mode)
            
            # We want strictly 0o600 (User RW, Group -, Other -)
            # We allow 0o400 (User R) as well.
            desired_mode = 0o600
            
            if (current_mode & 0o077) != 0: # Checks if group or others have any permissions
                # On Windows, os.chmod is limited. We log but don't force if it fails or looks weird.
                if sys.platform == "win32":
                    # Windows chmod doesn't support 0o600 fully (only read-only attribute).
                    # We skip the specific warning/enforcement to avoid noise/errors unless we implement ACLs.
                    pass 
                else:
                    logger.warning(f"Insecure config file permissions detected: {oct(current_mode)}. strictly enforcing 0600.")
                    os.chmod(path, desired_mode)
                    logger.info(f"Fixed permissions for {path} to 0600.")
                
        except Exception as e:
            logger.warning(f"Could not enforce permissions on {path}: {e}")

    @classmethod
    def load(cls) -> AuricConfig:
        """
        Loads the configuration from ~/.auric/auric.json.
        If the file doesn't exist, it creates a default one.
        """
        config_path = cls.get_config_path()
        
        # Security Check
        cls._ensure_permissions(config_path)

        if not config_path.exists():
            logger.info(f"No config found at {config_path}. Creating default.")
            default_config = AuricConfig()
            cls.save(default_config)
            return default_config

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                # Use json5 to allow comments
                data = json5.load(f)
            return AuricConfig(**data)
        except Exception as e:
            logger.error(f"Failed to load configuration from {config_path}: {e}")
            # Fallback to default or raise? For a security tool, failing fast is usually better.
            # But here we might return defaults if the file is just corrupt.
            # Let's re-raise to alert the user their config is broken.
            raise ValueError(f"Invalid configuration file: {e}")

    @classmethod
    def save(cls, config: AuricConfig) -> None:
        """Saves current configuration to disk."""
        config_path = cls.get_config_path()
        try:
            # We use 0o600 for open() to ensure file is created securely
            # os.open() with O_CREAT needing mode.
             # Ensure directory exists
            if not config_path.parent.exists():
                 config_path.parent.mkdir(parents=True, mode=0o700)

            # Write with json5 semantics (standard json dump for now as json5 doesn't have a dumper that preserves comments easily yet, 
            # but we produce standard json which is valid json5)
            # To strictly enforce creation permissions, we can use os.open
            
            # Prepare data
            data = config.model_dump(by_alias=True, mode='json')
            content = json5.dumps(data, indent=2)

            # Secure write
            if not config_path.exists():
                fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(content)
            else:
                 # Standard write if exists (permissions checked in load/_ensure_permissions)
                 # Re-enforce just in case
                 os.chmod(config_path, 0o600) 
                 with open(config_path, "w", encoding="utf-8") as f:
                     f.write(content)
                     
        except Exception as e:
            logger.error(f"Failed to save configuration to {config_path}: {e}")
            raise


# ==============================================================================
# Secrets Manager
# ==============================================================================

class SecretsManager:
    """
    Abstraction for retrieving sensitive secrets.
    Currently fetches from the loaded AuricConfig (which pulls from JSON).
    Future versions will integrate with system keyrings.
    """

    def __init__(self, config: AuricConfig):
        self.config = config

    def get_secret(self, key_name: str) -> Optional[str]:
        """
        Retrieves a secret by key name (dot notation supported).
        e.g., 'tools.openai.api_key'
        
        Priority:
        1. Environment Variable (AURIC_TOOLS_OPENAI_API_KEY) - Not implemented in this basic version but Good Practice
        2. Config File
        """
        # 1. Config File Lookup (Nested dictionary traversal)
        keys = key_name.split('.')
        value = self.config.model_dump(by_alias=True)
        try:
            for k in keys:
                value = value[k]
            
            if isinstance(value, (str, int, float, bool)):
                 return str(value)
            return None # Not a leaf value
        except (KeyError, TypeError) as e:
            logger.debug(f"Secret {key_name} not found in config: {e}")
            return None

# ==============================================================================
# Facade / Singleton Access
# ==============================================================================

_params: Optional[AuricConfig] = None
_secrets: Optional[SecretsManager] = None

def load_config() -> AuricConfig:
    """Global entry point to get the configuration."""
    global _params, _secrets
    if _params is None:
        _params = ConfigLoader.load()
        _secrets = SecretsManager(_params)
    return _params

def get_secrets_manager() -> SecretsManager:
    """Global entry point to get the secrets manager."""
    global _secrets, _params
    if _secrets is None:
        # Ensure config is loaded first
        if _params is None:
            load_config()
        # _secrets should be set by load_config, but for type safety:
        if _secrets is None and _params is not None:
             _secrets = SecretsManager(_params)
    
    if _secrets is None:
        raise RuntimeError("Failed to initialize SecretsManager")
        
    return _secrets
