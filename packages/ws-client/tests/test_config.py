"""Tests for ws_client.config — BackendConfig, BackendEntry, is_reachable, resolve_backend."""
import json
import pytest
import httpx
import respx
from pathlib import Path
from unittest.mock import patch, MagicMock

from ws_client.config import BackendConfig, BackendEntry, is_reachable, resolve_backend


# ── BackendEntry ──────────────────────────────────────────────────────────────

def test_health_url_appends_health():
    e = BackendEntry(name="test", api_url="http://server:7000")
    assert e.health_url() == "http://server:7000/health"


def test_health_url_strips_trailing_slash():
    e = BackendEntry(name="test", api_url="http://server:7000/")
    assert e.health_url() == "http://server:7000/health"


# ── BackendConfig.load ────────────────────────────────────────────────────────

def test_load_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = BackendConfig.load()
    assert cfg.backends == []


def test_load_parses_json(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backends": [{"name": "local", "api_url": "http://localhost:7000", "api_key": "", "compose_dir": None}]
    }))
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", config_file)
    cfg = BackendConfig.load()
    assert len(cfg.backends) == 1
    assert cfg.backends[0].name == "local"
    assert cfg.backends[0].api_url == "http://localhost:7000"


def test_load_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text("not json")
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", config_file)
    cfg = BackendConfig.load()
    assert cfg.backends == []


# ── BackendConfig.save / add / remove / get ───────────────────────────────────

def test_save_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_DIR", tmp_path)
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", config_file)
    cfg = BackendConfig(backends=[BackendEntry(name="x", api_url="http://x:7000")])
    cfg.save()
    data = json.loads(config_file.read_text())
    assert data["backends"][0]["name"] == "x"


def test_add_persists_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = BackendConfig()
    cfg.add(BackendEntry(name="new", api_url="http://new:7000"))
    assert cfg.get("new") is not None


def test_add_raises_on_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = BackendConfig()
    cfg.add(BackendEntry(name="dup", api_url="http://dup:7000"))
    with pytest.raises(ValueError, match="already exists"):
        cfg.add(BackendEntry(name="dup", api_url="http://dup2:7000"))


def test_remove_deletes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = BackendConfig()
    cfg.add(BackendEntry(name="todelete", api_url="http://x:7000"))
    cfg.remove("todelete")
    assert cfg.get("todelete") is None


def test_remove_raises_on_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = BackendConfig()
    with pytest.raises(KeyError):
        cfg.remove("nonexistent")


def test_get_returns_none_for_missing():
    cfg = BackendConfig()
    assert cfg.get("missing") is None


def test_get_returns_entry():
    entry = BackendEntry(name="found", api_url="http://found:7000")
    cfg = BackendConfig(backends=[entry])
    assert cfg.get("found") is entry


# ── is_reachable ──────────────────────────────────────────────────────────────

def test_is_reachable_true():
    entry = BackendEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        assert is_reachable(entry) is True


def test_is_reachable_false_non_200():
    entry = BackendEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(503)
        )
        assert is_reachable(entry) is False


def test_is_reachable_false_on_connection_error():
    entry = BackendEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert is_reachable(entry) is False


def test_is_reachable_sends_api_key_header():
    entry = BackendEntry(name="x", api_url="http://server:7000", api_key="mykey")
    with respx.mock:
        route = respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(200)
        )
        is_reachable(entry)
    assert route.calls.last.request.headers.get("x-api-key") == "mykey"


# ── resolve_backend ───────────────────────────────────────────────────────────

def test_resolve_backend_no_backends(tmp_path, monkeypatch):
    monkeypatch.setattr("ws_client.config.CONFIG_FILE", tmp_path / "config.json")
    result = resolve_backend(auto_start=False)
    assert result is None


def test_resolve_backend_returns_first_reachable(tmp_path, monkeypatch):
    entry = BackendEntry(name="local", api_url="http://local:7000")
    monkeypatch.setattr("ws_client.config.BackendConfig.load", lambda: BackendConfig(backends=[entry]))
    with patch("ws_client.config.is_reachable", return_value=True):
        result = resolve_backend(auto_start=False)
    assert result is entry


def test_resolve_backend_returns_none_when_unreachable_no_autostart(tmp_path, monkeypatch):
    entry = BackendEntry(name="local", api_url="http://local:7000")
    monkeypatch.setattr("ws_client.config.BackendConfig.load", lambda: BackendConfig(backends=[entry]))
    with patch("ws_client.config.is_reachable", return_value=False):
        result = resolve_backend(auto_start=False)
    assert result is None


def test_resolve_backend_skips_unreachable_tries_next(monkeypatch):
    e1 = BackendEntry(name="dead", api_url="http://dead:7000")
    e2 = BackendEntry(name="alive", api_url="http://alive:7000")
    monkeypatch.setattr("ws_client.config.BackendConfig.load",
                        lambda: BackendConfig(backends=[e1, e2]))
    reachable = {"http://alive:7000": True, "http://dead:7000": False}
    with patch("ws_client.config.is_reachable", side_effect=lambda e, **kw: reachable[e.api_url]):
        result = resolve_backend(auto_start=False)
    assert result is e2
