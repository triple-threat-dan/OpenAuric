from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.requests import Request
from starlette.status import HTTP_401_UNAUTHORIZED

from auric.core.config import AuricConfig

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Security(security), request: Request = None):
    """
    Verifies that the Bearer token matches the configured Web UI token.
    """
    # Get config from app state
    config: AuricConfig = request.app.state.config
    expected_token = config.gateway.web_ui_token
    
    if not expected_token:
        # Fail Secure: If no token is configured, reject access.
        # This forces the user (or daemon) to generate a token before using the API.
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication token not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != expected_token:
        # Lazy Reload: The token might have been updated via CLI (e.g., 'auric token new')
        # Check if re-loading config resolves the issue.
        from auric.core.config import ConfigLoader
        
        try:
            # Reload config from disk
            new_config = ConfigLoader.load()
            new_token = new_config.gateway.web_ui_token
            
            if new_token and credentials.credentials == new_token:
                # Token match after reload! Update the running app state.
                request.app.state.config = new_config
                return credentials
        except Exception:
            # Log error internally if needed, but fail secure externally
            pass
            
        # Still fails
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials
