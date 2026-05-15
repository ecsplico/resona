"""Tests for resona_client.config — EngineConfig, EngineEntry, is_reachable, resolve_engine."""
import json
import pytest
import httpx
import respx
from pathlib import Path
from unittest.mock import patch, MagicMock

from resona_client.config import EngineConfig, EngineEntry, is_reachable, resolve_engine


# ── EngineEntry ───────────────────────────────────────────────────────────────

def test_health_url_appends_health():
    e = EngineEntry(name="test", api_url="http://server:7000")
    assert e.health_url() == "http://server:7000/health"


def test_health_url_strips_trailing_slash():
    e = EngineEntry(name="test", api_url="http://server:7000/")
    assert e.health_url() == "http://server:7000/health"


# ── EngineConfig.load ─────────────────────────────────────────────────────────

def test_load_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "legacy_config.json")
    cfg = EngineConfig.load()
    assert cfg.engines == []


def test_load_parses_json(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "engines": [{"name": "local", "api_url": "http://localhost:7000", "api_key": "", "compose_dir": None}]
    }))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "legacy_config.json")
    cfg = EngineConfig.load()
    assert len(cfg.engines) == 1
    assert cfg.engines[0].name == "local"
    assert cfg.engines[0].api_url == "http://localhost:7000"


def test_load_returns_empty_on_invalid_json(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text("not json")
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "legacy_config.json")
    cfg = EngineConfig.load()
    assert cfg.engines == []


def test_load_migrates_from_legacy_config(tmp_path, monkeypatch):
    """When ~/.resona/config.json doesn't exist but ~/.whisper-server/config.json does, migrate it."""
    resona_dir = tmp_path / ".resona"
    resona_config = resona_dir / "config.json"
    legacy_config = tmp_path / ".whisper-server" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(json.dumps({
        "engines": [{"name": "migrated", "api_url": "http://legacy:7000", "api_key": "", "compose_dir": None}]
    }))

    monkeypatch.setattr("resona_client.config.CONFIG_DIR", resona_dir)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", resona_config)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", legacy_config)

    cfg = EngineConfig.load()
    assert len(cfg.engines) == 1
    assert cfg.engines[0].name == "migrated"
    # The new config file should now exist
    assert resona_config.exists()


def test_load_does_not_migrate_when_resona_config_exists(tmp_path, monkeypatch):
    """If ~/.resona/config.json already exists, legacy config is not used."""
    resona_dir = tmp_path / ".resona"
    resona_dir.mkdir()
    resona_config = resona_dir / "config.json"
    resona_config.write_text(json.dumps({
        "engines": [{"name": "current", "api_url": "http://current:7000", "api_key": "", "compose_dir": None}]
    }))
    legacy_config = tmp_path / ".whisper-server" / "config.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(json.dumps({
        "engines": [{"name": "legacy", "api_url": "http://legacy:7000", "api_key": "", "compose_dir": None}]
    }))

    monkeypatch.setattr("resona_client.config.CONFIG_DIR", resona_dir)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", resona_config)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", legacy_config)

    cfg = EngineConfig.load()
    assert len(cfg.engines) == 1
    assert cfg.engines[0].name == "current"


# ── EngineConfig.default_engine ───────────────────────────────────────────────

def test_load_default_engine_from_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "engines": [],
        "default_engine": "voxtral",
    }))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")
    cfg = EngineConfig.load()
    assert cfg.default_engine == "voxtral"


def test_load_default_engine_falls_back_to_faster_whisper(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"engines": []}))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")
    cfg = EngineConfig.load()
    assert cfg.default_engine == "faster-whisper"


def test_save_persists_default_engine(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    cfg = EngineConfig(default_engine="whisper")
    cfg.save()
    data = json.loads(config_file.read_text())
    assert data["default_engine"] == "whisper"


def test_load_reads_legacy_backends_key(tmp_path, monkeypatch):
    """A config.json using the old `backends`/`default_backend` keys still loads."""
    config_file = tmp_path / "config.json"
    legacy = tmp_path / "legacy-never-created"
    config_file.write_text(json.dumps({
        "backends": [{"name": "old", "api_url": "http://old:7000", "api_key": "", "compose_dir": None}],
        "default_backend": "voxtral",
    }))
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_DIR", legacy)
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", legacy / "config.json")
    cfg = EngineConfig.load()
    assert len(cfg.engines) == 1
    assert cfg.engines[0].name == "old"
    assert cfg.default_engine == "voxtral"


# ── EngineConfig.save / add / remove / get ────────────────────────────────────

def test_save_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", config_file)
    cfg = EngineConfig(engines=[EngineEntry(name="x", api_url="http://x:7000")])
    cfg.save()
    data = json.loads(config_file.read_text())
    assert data["engines"][0]["name"] == "x"


def test_add_persists_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    cfg.add(EngineEntry(name="new", api_url="http://new:7000"))
    assert cfg.get("new") is not None


def test_add_raises_on_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    cfg.add(EngineEntry(name="dup", api_url="http://dup:7000"))
    with pytest.raises(ValueError, match="already exists"):
        cfg.add(EngineEntry(name="dup", api_url="http://dup2:7000"))


def test_remove_deletes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    cfg.add(EngineEntry(name="todelete", api_url="http://x:7000"))
    cfg.remove("todelete")
    assert cfg.get("todelete") is None


def test_remove_raises_on_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    cfg = EngineConfig()
    with pytest.raises(KeyError):
        cfg.remove("nonexistent")


def test_get_returns_none_for_missing():
    cfg = EngineConfig()
    assert cfg.get("missing") is None


def test_get_returns_entry():
    entry = EngineEntry(name="found", api_url="http://found:7000")
    cfg = EngineConfig(engines=[entry])
    assert cfg.get("found") is entry


# ── is_reachable ──────────────────────────────────────────────────────────────

def test_is_reachable_true():
    entry = EngineEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        assert is_reachable(entry) is True


def test_is_reachable_false_non_200():
    entry = EngineEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(503)
        )
        assert is_reachable(entry) is False


def test_is_reachable_false_on_connection_error():
    entry = EngineEntry(name="x", api_url="http://server:7000")
    with respx.mock:
        respx.get("http://server:7000/health").mock(
            side_effect=httpx.ConnectError("refused")
        )
        assert is_reachable(entry) is False


def test_is_reachable_sends_api_key_header():
    entry = EngineEntry(name="x", api_url="http://server:7000", api_key="mykey")
    with respx.mock:
        route = respx.get("http://server:7000/health").mock(
            return_value=httpx.Response(200)
        )
        is_reachable(entry)
    assert route.calls.last.request.headers.get("x-api-key") == "mykey"


# ── resolve_engine ────────────────────────────────────────────────────────────

def test_resolve_engine_no_engines(tmp_path, monkeypatch):
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "legacy_config.json")
    result = resolve_engine(auto_start=False)
    assert result is None


def test_resolve_engine_returns_first_reachable(tmp_path, monkeypatch):
    entry = EngineEntry(name="local", api_url="http://local:7000")
    monkeypatch.setattr("resona_client.config.EngineConfig.load", lambda: EngineConfig(engines=[entry]))
    with patch("resona_client.config.is_reachable", return_value=True):
        result = resolve_engine(auto_start=False)
    assert result is entry


def test_resolve_engine_returns_none_when_unreachable_no_autostart(tmp_path, monkeypatch):
    entry = EngineEntry(name="local", api_url="http://local:7000")
    monkeypatch.setattr("resona_client.config.EngineConfig.load", lambda: EngineConfig(engines=[entry]))
    with patch("resona_client.config.is_reachable", return_value=False):
        result = resolve_engine(auto_start=False)
    assert result is None


def test_resolve_engine_skips_unreachable_tries_next(monkeypatch):
    e1 = EngineEntry(name="dead", api_url="http://dead:7000")
    e2 = EngineEntry(name="alive", api_url="http://alive:7000")
    monkeypatch.setattr("resona_client.config.EngineConfig.load",
                        lambda: EngineConfig(engines=[e1, e2]))
    reachable = {"http://alive:7000": True, "http://dead:7000": False}
    with patch("resona_client.config.is_reachable", side_effect=lambda e, **kw: reachable[e.api_url]):
        result = resolve_engine(auto_start=False)
    assert result is e2


# ── Round-trip tests ───────────────────────────────────────────────────────────

def test_config_round_trip_preserves_all_fields(tmp_path, monkeypatch):
    """Save a config with engines and default_engine, load it back, verify all fields."""
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")

    original = EngineConfig(
        engines=[
            EngineEntry(name="gpu-server", api_url="http://gpu:7000", api_key="secret"),
            EngineEntry(name="local", api_url="http://localhost:7000", compose_dir="/opt/resona"),
        ],
        default_engine="voxtral",
    )
    original.save()

    loaded = EngineConfig.load()
    assert len(loaded.engines) == 2
    assert loaded.engines[0].name == "gpu-server"
    assert loaded.engines[0].api_key == "secret"
    assert loaded.engines[1].compose_dir == "/opt/resona"
    assert loaded.default_engine == "voxtral"


def test_config_round_trip_with_ssh_fields(tmp_path, monkeypatch):
    """SSH-related fields survive save/load."""
    monkeypatch.setattr("resona_client.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("resona_client.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("resona_client.config._LEGACY_CONFIG_FILE", tmp_path / "nope.json")

    original = EngineConfig(
        engines=[
            EngineEntry(
                name="remote",
                api_url="http://localhost:7000",
                ssh_host="user@myserver.com:2222",
                ssh_remote_port=7000,
            ),
        ],
    )
    original.save()

    loaded = EngineConfig.load()
    assert loaded.engines[0].ssh_host == "user@myserver.com:2222"
    assert loaded.engines[0].ssh_remote_port == 7000
