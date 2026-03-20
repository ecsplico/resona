"""API Key Authentication for ws-api."""
import logging
from typing import Optional
from decouple import config
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key() -> str:
    api_key = config("API_KEY", default=None)
    if not api_key:
        logger.error("API_KEY not configured in environment")
        raise RuntimeError("API_KEY must be configured in environment variables")
    return api_key


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    expected_key = get_api_key()

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
