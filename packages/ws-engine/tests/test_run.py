"""Test that the ws-engine entrypoint respects the PORT env var."""
import importlib
from unittest.mock import patch
import pytest


def test_run_uses_port_from_env(monkeypatch):
    """PORT env var should override the default 7001."""
    monkeypatch.setenv("PORT", "9876")
    monkeypatch.setenv("LOGLEVEL", "warning")

    import ws_engine.run as run_mod
    importlib.reload(run_mod)  # re-evaluates module-level config() with new env

    with patch("ws_engine.run.uvicorn.run") as mock_run:
        run_mod.main()

    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 9876


def test_run_defaults_to_7001(monkeypatch):
    """Without PORT set, default is 7001."""
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("LOGLEVEL", "warning")

    import ws_engine.run as run_mod
    importlib.reload(run_mod)

    with patch("ws_engine.run.uvicorn.run") as mock_run:
        run_mod.main()

    _, kwargs = mock_run.call_args
    assert kwargs.get("port") == 7001
