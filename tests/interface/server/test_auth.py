import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from starlette.status import HTTP_401_UNAUTHORIZED
from fastapi.security import HTTPAuthorizationCredentials

from auric.interface.server.auth import verify_token
from auric.core.config import AuricConfig

@pytest.fixture
def mock_request():
    request = MagicMock()
    # Mocking the nested structure manually
    config = MagicMock()
    config.gateway = MagicMock()
    request.app.state.config = config
    return request

@pytest.fixture
def mock_credentials():
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid_token")

@pytest.mark.asyncio
async def test_verify_token_success(mock_request, mock_credentials):
    # Setup
    mock_request.app.state.config.gateway.web_ui_token = "valid_token"
    
    # Execute
    result = await verify_token(credentials=mock_credentials, request=mock_request)
    
    # Assert
    assert result == mock_credentials

@pytest.mark.asyncio
async def test_verify_token_missing_config_token(mock_request, mock_credentials):
    # Setup: config has no token
    mock_request.app.state.config.gateway.web_ui_token = None
    
    # Execute & Assert
    with pytest.raises(HTTPException) as exc_info:
        await verify_token(credentials=mock_credentials, request=mock_request)
    
    assert exc_info.value.status_code == HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == "Authentication token not configured"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}

@pytest.mark.asyncio
async def test_verify_token_invalid_initial_token_reloads_success(mock_request, mock_credentials):
    # Setup: initial token mismatch
    mock_request.app.state.config.gateway.web_ui_token = "old_token"
    
    new_config = MagicMock()
    new_config.gateway = MagicMock()
    new_config.gateway.web_ui_token = "valid_token"
    
    with patch("auric.core.config.ConfigLoader.load", return_value=new_config):
        # Execute
        result = await verify_token(credentials=mock_credentials, request=mock_request)
        
        # Assert
        assert result == mock_credentials
        # Ensure the app state was updated with new config
        assert mock_request.app.state.config == new_config

@pytest.mark.asyncio
async def test_verify_token_invalid_initial_token_reloads_failure(mock_request, mock_credentials):
    # Setup: initial token mismatch
    mock_request.app.state.config.gateway.web_ui_token = "old_token"
    
    new_config = MagicMock()
    new_config.gateway = MagicMock()
    new_config.gateway.web_ui_token = "still_wrong_token"
    
    with patch("auric.core.config.ConfigLoader.load", return_value=new_config):
        # Execute & Assert
        with pytest.raises(HTTPException) as exc_info:
            await verify_token(credentials=mock_credentials, request=mock_request)
        
        assert exc_info.value.status_code == HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid authentication token"

@pytest.mark.asyncio
async def test_verify_token_invalid_initial_token_reload_exception(mock_request, mock_credentials):
    # Setup: initial token mismatch
    mock_request.app.state.config.gateway.web_ui_token = "old_token"
    
    with patch("auric.core.config.ConfigLoader.load", side_effect=Exception("Disk Error")):
        # Execute & Assert
        with pytest.raises(HTTPException) as exc_info:
            await verify_token(credentials=mock_credentials, request=mock_request)
        
        assert exc_info.value.status_code == HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid authentication token"

@pytest.mark.asyncio
async def test_verify_token_invalid_token_no_reload_on_mismatch_persists(mock_request, mock_credentials):
    # Setup: initial token mismatch, and reload also returns no token
    mock_request.app.state.config.gateway.web_ui_token = "old_token"
    
    new_config = MagicMock()
    new_config.gateway = MagicMock()
    new_config.gateway.web_ui_token = None # Reloaded config has no token
    
    with patch("auric.core.config.ConfigLoader.load", return_value=new_config):
        # Execute & Assert
        with pytest.raises(HTTPException) as exc_info:
            await verify_token(credentials=mock_credentials, request=mock_request)
        
        assert exc_info.value.status_code == HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail == "Invalid authentication token"
