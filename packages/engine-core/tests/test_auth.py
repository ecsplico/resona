import pytest
from unittest.mock import patch
from fastapi import HTTPException
from resona_engine_core.auth import verify_api_key


@pytest.mark.anyio
@patch("resona_engine_core.auth.config", return_value=None)
async def test_auth_disabled_when_no_key(mock_config):
    result = await verify_api_key(api_key=None)
    assert result is None


@pytest.mark.anyio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_valid_key_passes(mock_config):
    result = await verify_api_key(api_key="secret")
    assert result == "secret"


@pytest.mark.anyio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_missing_key_raises_401(mock_config):
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(api_key=None)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
@patch("resona_engine_core.auth.config", return_value="secret")
async def test_wrong_key_raises_401(mock_config):
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(api_key="wrong")
    assert exc_info.value.status_code == 401
