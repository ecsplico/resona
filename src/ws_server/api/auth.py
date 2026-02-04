"""
API Key Authentication for Whisper Server.

This module provides simple API key-based authentication for API endpoints.
"""
import logging
from typing import Optional
from decouple import config
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

# Define the API key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key() -> str:
    """
    Retrieves the API key from environment configuration.
    
    Returns:
        The configured API key.
    
    Raises:
        RuntimeError: If API key is not configured.
    """
    api_key = config("API_KEY", default=None)
    if not api_key:
        logger.error("API_KEY not configured in environment")
        raise RuntimeError("API_KEY must be configured in environment variables")
    return api_key


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """
    Validates the provided API key against the configured key.
    
    Args:
        api_key: The API key from the request header.
    
    Returns:
        The validated API key.
    
    Raises:
        HTTPException: If the API key is missing or invalid.
    """
    expected_key = get_api_key()
    
    if api_key is None:
        logger.warning("API request without API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    if api_key != expected_key:
        logger.warning(f"Invalid API key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    
    return api_key
