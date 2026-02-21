"""
Configuration and Secrets Management for OpenAuric.

This module handles loading configuration from disk (.auric/auric.json),
enforcing security permissions, and providing access to secrets.
"""

import os
import sys
import stat
import logging
import json
from pathlib import Path
from typing import Dict, Any, Optional, List

import json5
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger("auric.config")

# ==============================================================================
# Constants & Root Discovery
# ==============================================================================

def find_auric_root() -> Path:
    """
    Locates the .auric directory. 
    Prioritizes the AURIC_ROOT environment variable, otherwise defaults 
    strictly to the current directory's .auric folder.
    """
    if env_root := os.getenv("AURIC_ROOT"):
        root = Path(env_root)
    else:
        root = Path.cwd() / ".auric"
    
    # We log the selection later during daemon startup or CLI initialization
    return root

AURIC_CONFIG_FILE = "auric.json"
AURIC_ROOT = find_auric_root()
AURIC_WORKSPACE_DIR = AURIC_ROOT / "workspace"
AURIC_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


# ==============================================================================
# Pydantic Models
# ==============================================================================

class HeartbeatConfig(BaseModel):
    """Configuration for the agent heartbeat mechanism."""
    enabled: bool = True
    interval: str = "30m"
    active_hours: str = Field(default="09:00-18:00", alias="activeHours")
    target: str = "console"

class LoggingConfig(BaseModel):
    """Configuration for system-wide JSONL logging."""
    enabled: bool = True
    max_size_mb: int = 10
    backup_count: int = 5
    log_dir: str = ".auric/logs"

class AgentDefaults(BaseModel):
    """Default settings for agents."""
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

class ModelConfig(BaseModel):
    """Configuration for a specific model."""
    provider: str
    model: str
    enabled: bool = True

class AgentsConfig(BaseModel):
    """Configuration for agents."""
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    name: str = "Auric"
    is_local: bool = False
    max_recursion: int = 2
    max_cost: float = 1.0
    max_turns: int = 15
    dream_time: str = "04:00" # 24h format
    enable_dream_stories: bool = True
    
    models: Dict[str, ModelConfig] = Field(default_factory=lambda: {
        "smart_model": ModelConfig(provider="gemini", model="gemini/gemini-2.5-pro"),
        "fast_model": ModelConfig(provider="gemini", model="gemini/gemini-2.5-flash"),
        "heartbeat_model": ModelConfig(provider="gemini", model="gemini/gemini-2.5-flash"),
        "embeddings_model": ModelConfig(provider="auto", model="models/text-embedding-004")
    })

class GatewayConfig(BaseModel):
    """Configuration for the API gateway."""
    port: int = 8067
    host: str = "127.0.0.1"
    web_ui_token: Optional[str] = None
    disable_access_log: bool = False

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
    bot_loop_limit: int = 4

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
    brave: Optional[str] = None

class EmbeddingsConfig(BaseModel):
    """Configuration for embedding models."""
    provider: str = "auto"
    model: Optional[str] = None

class AuricConfig(BaseSettings):
    """Root configuration object for OpenAuric."""
    debug: bool = False
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    pacts: PactsConfig = Field(default_factory=PactsConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    keys: LLMKeys = Field(default_factory=LLMKeys) 
    tools: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(strict=True, populate_by_name=True)


# ==============================================================================
# Configuration Loader
# ==============================================================================

class ConfigLoader:
    """Responsible for locating, validating, and loading the configuration."""
    
    DEFAULT_CONFIG_DIR = AURIC_ROOT
    CONFIG_FILENAME = "auric.json"

    @classmethod
    def get_config_path(cls) -> Path:
        return cls.DEFAULT_CONFIG_DIR / cls.CONFIG_FILENAME

    @classmethod
    def _ensure_permissions(cls, path: Path) -> None:
        """Enforce 0600 permissions (Owner Read/Write only)."""
        if not path.parent.exists():
            try:
                path.parent.mkdir(parents=True, mode=0o700)
            except Exception as e:
                logger.error(f"Failed to create config directory {path.parent}: {e}")
                raise

        if not path.exists():
            return

        try:
            current_mode = stat.S_IMODE(path.stat().st_mode)
            # strictly 0o600 (User RW) or 0o400 (User R)
            if (current_mode & 0o077) != 0:
                if sys.platform != "win32":
                    logger.warning(f"Insecure config file permissions: {oct(current_mode)}. Enforcing 0600.")
                    os.chmod(path, 0o600)
                    logger.info(f"Fixed permissions for {path} to 0600.")
        except Exception as e:
            logger.warning(f"Could not enforce permissions on {path}: {e}")

    @classmethod
    def load(cls) -> AuricConfig:
        """Loads .auric/auric.json. Creates default if missing."""
        config_path = cls.get_config_path()
        cls._ensure_permissions(config_path)

        if not config_path.exists():
            logger.info(f"No config found at {config_path}. Creating default.")
            default_config = AuricConfig()
            cls.save(default_config)
            return default_config

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json5.load(f)
            return AuricConfig(**data)
        except Exception as e:
            logger.error(f"Failed to load configuration from {config_path}: {e}")
            raise ValueError(f"Invalid configuration file: {e}")

    @classmethod
    def save(cls, config: AuricConfig) -> None:
        """Saves configuration to disk with 0600 permissions."""
        config_path = cls.get_config_path()
        try:
            if not config_path.parent.exists():
                 config_path.parent.mkdir(parents=True, mode=0o700)

            data = config.model_dump(by_alias=True, mode='json')
            content = json.dumps(data, indent=2)

            # Secure write
            if not config_path.exists():
                fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    f.write(content)
            else:
                 try:
                     os.chmod(config_path, 0o600)
                 except Exception:
                     pass
                 with open(config_path, "w", encoding="utf-8") as f:
                     f.write(content)
        except Exception as e:
            logger.error(f"Failed to save configuration to {config_path}: {e}")
            raise


# ==============================================================================
# Secrets Manager
# ==============================================================================

class SecretsManager:
    """Abstraction for retrieving sensitive secrets from config."""

    def __init__(self, config: AuricConfig):
        self.config = config

    def get_secret(self, key_name: str) -> Optional[str]:
        """Retrieves a secret by dot-notation key (e.g. 'tools.openai.api_key')."""
        keys = key_name.split('.')
        value = self.config.model_dump(by_alias=True)
        try:
            for k in keys:
                value = value[k]
            
            if isinstance(value, (str, int, float, bool)):
                 return str(value)
            return None
        except (KeyError, TypeError) as e:
            logger.debug(f"Secret {key_name} not found in config: {e}")
            return None


# ==============================================================================
# Facade / Singleton Access
# ==============================================================================

_params: Optional[AuricConfig] = None
_secrets: Optional[SecretsManager] = None

def load_config() -> AuricConfig:
    """Global configuration accessor."""
    global _params, _secrets
    if _params is None:
        _params = ConfigLoader.load()
        _secrets = SecretsManager(_params)
    return _params

def get_secrets_manager() -> SecretsManager:
    """Global secrets manager accessor."""
    global _secrets, _params
    if _secrets is None:
        load_config()
    if _secrets is None:
        raise RuntimeError("Failed to initialize SecretsManager")
    return _secrets
