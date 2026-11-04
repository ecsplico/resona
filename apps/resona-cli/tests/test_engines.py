"""Tests for resona_cli.engines CLI commands."""
import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


@pytest.fixture
def isolated_config(tmp_path):
    """Redirect all engine-config I/O to tmp_path.

    This patches the legacy paths too: ``EngineConfig.load()`` migrates
    ``~/.whisper-server/config.json`` into the active config when the latter
    is absent. Without isolating the legacy path, tests that start with no
    config file would migrate the developer's real legacy config into the
    test. The legacy path points at a directory that is never created, so the
    migration branch never triggers.

    Yields the active config-file Path so tests can seed or inspect it.
    """
    config_file = tmp_path / "config.json"
    legacy_dir = tmp_path / "legacy-never-created"
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_client.config._LEGACY_CONFIG_DIR", legacy_dir),
        patch("resona_client.config._LEGACY_CONFIG_FILE", legacy_dir / "config.json"),
    ):
        yield config_file


def _write_engines(config_file, *entries):
    """Seed the config file with the given engine dicts."""
    config_file.write_text(json.dumps({"engines": list(entries)}))


def _engine(name, api_url, api_key="", compose_dir=None):
    return {"name": name, "api_url": api_url, "api_key": api_key, "compose_dir": compose_dir}


# ── engines list ──────────────────────────────────────────────────────────────

def test_list_no_engines(isolated_config):
    result = runner.invoke(app, ["engines", "list"])
    # merged view always shows built-in engines even when no config entries exist
    assert "faster-whisper" in result.output
    assert "built-in" in result.output


def test_list_shows_engines(isolated_config):
    _write_engines(isolated_config, _engine("local", "http://localhost:7000"))
    with patch("resona_cli.engines.is_reachable", return_value=False):
        result = runner.invoke(app, ["engines", "list"])
    assert "local" in result.output


# ── engines add ───────────────────────────────────────────────────────────────

def test_add_engine(isolated_config):
    with patch("resona_cli.engines.is_reachable", return_value=False):
        result = runner.invoke(app, ["engines", "add", "myserver", "http://myserver:7000"])
    assert result.exit_code == 0
    assert "myserver" in result.output

    data = json.loads(isolated_config.read_text())
    assert data["engines"][0]["name"] == "myserver"


def test_add_engine_with_key(isolated_config):
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, ["engines", "add", "secure", "http://s:7000", "--key", "abc123"])
    assert result.exit_code == 0
    data = json.loads(isolated_config.read_text())
    assert data["engines"][0]["api_key"] == "abc123"


def test_add_engine_duplicate_fails(isolated_config):
    with patch("resona_cli.engines.is_reachable", return_value=False):
        runner.invoke(app, ["engines", "add", "dup", "http://dup:7000"])
        result = runner.invoke(app, ["engines", "add", "dup", "http://dup:7001"])
    assert result.exit_code == 1


def test_added_hub_is_used_without_env(isolated_config, monkeypatch):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("RESONA_API_KEY", raising=False)
    with patch("resona_cli.engines.is_reachable", return_value=True):
        runner.invoke(app, ["engines", "add", "prod", "https://hub.example/", "--key", "k"])

    from resona_cli.hub import hub_identity

    assert hub_identity() == ("https://hub.example", "k")


def test_unreachable_chosen_hub_is_an_error_without_trying_another(isolated_config, monkeypatch):
    import httpx
    import typer

    from resona_cli.hub import connect_hub, hub_identity

    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("WS_API_URL", raising=False)
    _write_engines(isolated_config,
                   _engine("first", "https://down.example", "k1"),
                   _engine("second", "https://up.example", "k2"))
    probed = []

    def fake_get(url, **kwargs):
        probed.append(url)
        if "down.example" in url:
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    with patch("resona_client.config.httpx.get", side_effect=fake_get):
        assert hub_identity() == ("https://down.example", "k1")
        with pytest.raises(typer.Exit) as exc:
            connect_hub()
        assert exc.value.exit_code == 1

    assert probed and all("up.example" not in url for url in probed)


def test_connected_hub_matches_identity(isolated_config, monkeypatch):
    import httpx

    from resona_cli.hub import connect_hub, hub_identity

    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("WS_API_URL", raising=False)
    _write_engines(isolated_config,
                   _engine("first", "https://a.example", "k1"),
                   _engine("second", "https://b.example", "k2"))

    with patch("resona_client.config.httpx.get", return_value=httpx.Response(200)):
        client = connect_hub()

    assert (client.base_url, client.api_key) == hub_identity()

def test_ws_api_url_alone_drives_identity_and_connection(isolated_config, monkeypatch):
    from resona_cli.hub import connect_hub, hub_identity

    for var in ("RESONA_API_URL", "RESONA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WS_API_URL", "https://ws.example/")
    monkeypatch.setenv("WS_API_KEY", "wk")
    _write_engines(isolated_config, _engine("first", "https://a.example", "k1"))

    client = connect_hub()

    assert hub_identity() == ("https://ws.example", "wk")
    assert (client.base_url, client.api_key) == hub_identity()

# ── engines remove ────────────────────────────────────────────────────────────

def test_remove_engine(isolated_config):
    _write_engines(isolated_config, _engine("todelete", "http://x:7000"))
    result = runner.invoke(app, ["engines", "remove", "todelete"])
    assert result.exit_code == 0
    assert "todelete" in result.output

    data = json.loads(isolated_config.read_text())
    assert data["engines"] == []


def test_remove_nonexistent_engine_fails(isolated_config):
    result = runner.invoke(app, ["engines", "remove", "ghost"])
    assert result.exit_code == 1


# ── engines test ──────────────────────────────────────────────────────────────

def test_test_engines_no_engines(isolated_config):
    result = runner.invoke(app, ["engines", "test"])
    assert result.exit_code == 1


def test_test_engines_reachable_exits_0(isolated_config):
    _write_engines(isolated_config, _engine("ok", "http://ok:7000"))
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, ["engines", "test"])
    assert result.exit_code == 0


def test_test_engines_unreachable_exits_1(isolated_config):
    _write_engines(isolated_config, _engine("dead", "http://dead:7000"))
    with patch("resona_cli.engines.is_reachable", return_value=False):
        result = runner.invoke(app, ["engines", "test"])
    assert result.exit_code == 1


def test_test_specific_engine_not_found(isolated_config):
    _write_engines(isolated_config, _engine("existing", "http://x:7000"))
    result = runner.invoke(app, ["engines", "test", "ghost"])
    assert result.exit_code == 1


# ── engines list — merged view ────────────────────────────────────────────────

def test_list_shows_builtin_local_engines_when_no_config(isolated_config):
    result = runner.invoke(app, ["engines", "list"])
    assert "faster-whisper" in result.output
    assert "whisper" in result.output
    assert "voxtral" in result.output
    assert "built-in" in result.output


def test_list_shows_config_entries_alongside_builtins(isolated_config):
    _write_engines(isolated_config, _engine("my-gpu-box", "http://gpu:7000"))
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, ["engines", "list"])
    assert "my-gpu-box" in result.output
    assert "hub" in result.output
    assert "reachable" in result.output


# ── engines add — collision ───────────────────────────────────────────────────

def test_add_rejects_name_shadowing_builtin_engine(isolated_config):
    result = runner.invoke(app, ["engines", "add", "whisper", "http://gpu:7000"])
    assert result.exit_code == 1
    assert "built-in" in result.output.lower()


# ── engines status ────────────────────────────────────────────────────────────

def test_engines_status_shows_catalogue(isolated_config):
    catalogue = {
        "engines": [
            {"name": "faster-whisper", "kind": "local", "capabilities": ["stt"],
             "private": True, "available": True, "models": ["large-v3"]},
            {"name": "openai", "kind": "cloud", "capabilities": ["stt", "tts"],
             "private": False, "available": True, "models": ["whisper-1", "tts-1"]},
        ],
        "default": "faster-whisper",
    }
    mock_client = MagicMock()
    mock_client.list_engines.return_value = catalogue

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["engines", "status"])

    assert result.exit_code == 0
    assert "faster-whisper" in result.output
    assert "openai" in result.output


def test_engines_status_no_server_exits_nonzero(isolated_config):
    with patch("resona_client.client.ResonaClient.from_config",
                side_effect=RuntimeError("no server")):
        result = runner.invoke(app, ["engines", "status"])
    assert result.exit_code != 0


def test_engines_status_marks_default(isolated_config):
    catalogue = {
        "engines": [
            {"name": "faster-whisper", "kind": "local", "capabilities": ["stt"],
             "private": True, "available": True, "models": ["large-v3"]},
        ],
        "default": "faster-whisper",
    }
    mock_client = MagicMock()
    mock_client.list_engines.return_value = catalogue

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["engines", "status"])

    assert result.exit_code == 0
    assert "default" in result.output


def test_engines_status_empty_catalogue(isolated_config):
    mock_client = MagicMock()
    mock_client.list_engines.return_value = {"engines": [], "default": None}

    with patch("resona_client.client.ResonaClient.from_config", return_value=mock_client):
        result = runner.invoke(app, ["engines", "status"])

    assert result.exit_code == 0
    assert "No engines" in result.output


def test_engines_status_gateway_error_exits_nonzero(isolated_config):
    import httpx
    with patch("resona_client.client.ResonaClient.from_config",
               side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(app, ["engines", "status"])
    assert result.exit_code != 0
