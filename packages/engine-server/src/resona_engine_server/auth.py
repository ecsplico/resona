"""API key authentication for Resona engine."""

import logging
from typing import Optional

from decouple import config
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

log = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    api_key: Optional[str] = Security(api_key_header),
) -> Optional[str]:
    """Validate API key. If RESONA_ENGINE_KEY is not set, auth is disabled."""
    expected = config("RESONA_ENGINE_KEY", default=None)

    if not expected:
        return None

    if api_key is None:
        log.warning("Engine API request without API key")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != expected:
        log.warning("Invalid engine API key attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key
