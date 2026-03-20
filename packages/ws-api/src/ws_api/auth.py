"""API Key Authentication for ws-api."""
import logging
from typing import Optional
from decouple import config
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key() -> Optional[str]:
    """Retrieve WS_API_KEY from environment. Returns None if not configured (auth disabled)."""
    return config("WS_API_KEY", default=None)


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    """Validate the provided API key. If WS_API_KEY is not set, auth is disabled."""
    expected_key = get_api_key()

    if not expected_key:
        return None  # Auth disabled

    if api_key is None:
        logger.warning("API request without API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != expected_key:
        logger.warning("Invalid API key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key
