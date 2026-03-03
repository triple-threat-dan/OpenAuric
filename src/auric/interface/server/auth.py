"""
Authentication middleware for the OpenAuric API.

Provides Bearer token verification with support for lazy configuration 
reloading to handle dynamic token updates.
"""

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import Request
from starlette.status import HTTP_401_UNAUTHORIZED

from auric.core.config import AuricConfig

security = HTTPBearer()

async def verify_token(
    credentials: HTTPAuthorizationCredentials = Security(security), 
    request: Request = None
) -> HTTPAuthorizationCredentials:
    """
    Verifies the Bearer token against the configured Web UI token.
    
    If verification fails, attempts to reload the configuration from disk 
    to account for updates made via the CLI.
    """
    config: AuricConfig = request.app.state.config
    expected_token = config.gateway.web_ui_token
    
    if not expected_token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication token not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials == expected_token:
        return credentials

    # Lazy Reload: Check if token was updated on disk (e.g. via 'auric token new')
    from auric.core.config import ConfigLoader
    try:
        new_config = ConfigLoader.load()
        if new_config.gateway.web_ui_token == credentials.credentials:
            # Sync new config to app state and approve
            request.app.state.config = new_config
            return credentials
    except Exception:
        pass
        
    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )
