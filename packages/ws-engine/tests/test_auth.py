"""Tests for ws_engine.auth — API key validation."""
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastapi import FastAPI, Depends
from typing import Optional

from ws_engine.auth import verify_api_key, get_api_key


# ── Minimal test app to exercise the dependency ───────────────────────────

def make_app():
    app = FastAPI()

    @app.get("/protected")
    async def protected(key=Depends(verify_api_key)):
        return {"ok": True}

    return app


def test_auth_disabled_when_no_env_var():
    """No ENGINE_API_KEY → all requests pass."""
    with patch("ws_engine.auth.get_api_key", return_value=None):
        client = TestClient(make_app())
        resp = client.get("/protected")
    assert resp.status_code == 200


def test_auth_disabled_request_without_header():
    with patch("ws_engine.auth.get_api_key", return_value=None):
        client = TestClient(make_app())
        resp = client.get("/protected")
    assert resp.status_code == 200


def test_auth_enabled_correct_key():
    with patch("ws_engine.auth.get_api_key", return_value="secret"):
        client = TestClient(make_app())
        resp = client.get("/protected", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200


def test_auth_enabled_wrong_key():
    with patch("ws_engine.auth.get_api_key", return_value="secret"):
        client = TestClient(make_app())
        resp = client.get("/protected", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
    assert "Invalid API Key" in resp.json()["detail"]


def test_auth_enabled_missing_key():
    with patch("ws_engine.auth.get_api_key", return_value="secret"):
        client = TestClient(make_app())
        resp = client.get("/protected")
    assert resp.status_code == 401
    assert "Missing API Key" in resp.json()["detail"]


def test_get_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("ENGINE_API_KEY", "mykey")
    # Re-read via decouple — force re-evaluation
    import importlib
    import ws_engine.auth as auth_mod
    importlib.reload(auth_mod)
    # get_api_key should return the env value
    from ws_engine.auth import get_api_key as fresh_get
    assert fresh_get() == "mykey"
