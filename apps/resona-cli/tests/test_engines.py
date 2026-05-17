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
    isolated_config.write_text(json.dumps({"engines": [
        {"name": "my-gpu-box", "api_url": "http://gpu:7000", "private": True},
        {"name": "deepgram", "type": "cloud", "provider": "deepgram"},
    ]}))
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, ["engines", "list"])
    assert "my-gpu-box" in result.output
    assert "server" in result.output
    assert "deepgram" in result.output
    assert "cloud" in result.output


def test_list_marks_local_engines_private(isolated_config):
    result = runner.invoke(app, ["engines", "list"])
    # the three local engines are always private
    for line in result.output.splitlines():
        if "faster-whisper" in line:
            assert "yes" in line


# ── engines add — cloud + collision ───────────────────────────────────────────

def test_add_cloud_engine(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--model", "nova-3", "--option", "smart_format=true",
    ])
    assert result.exit_code == 0
    data = json.loads(isolated_config.read_text())
    entry = data["engines"][0]
    assert entry["type"] == "cloud"
    assert entry["provider"] == "deepgram"
    assert entry["model"] == "nova-3"
    assert entry["options"] == {"smart_format": "true"}


def test_add_cloud_engine_repeatable_option(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--option", "smart_format=true", "--option", "diarize=false",
    ])
    assert result.exit_code == 0
    opts = json.loads(isolated_config.read_text())["engines"][0]["options"]
    assert opts == {"smart_format": "true", "diarize": "false"}


def test_add_cloud_engine_unknown_provider_rejected(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "bad", "--type", "cloud", "--provider", "nonsense",
    ])
    assert result.exit_code == 1
    assert "provider" in result.output.lower()


def test_add_private_resona_api_engine(isolated_config):
    with patch("resona_cli.engines.is_reachable", return_value=True):
        result = runner.invoke(app, [
            "engines", "add", "gpu", "http://gpu:7000", "--private",
        ])
    assert result.exit_code == 0
    assert json.loads(isolated_config.read_text())["engines"][0]["private"] is True


def test_add_rejects_name_shadowing_builtin_engine(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "whisper", "--type", "cloud", "--provider", "openai",
    ])
    assert result.exit_code == 1
    assert "built-in" in result.output.lower()


def test_add_option_bad_format_rejected(isolated_config):
    result = runner.invoke(app, [
        "engines", "add", "dg", "--type", "cloud", "--provider", "deepgram",
        "--option", "noequalsign",
    ])
    assert result.exit_code == 1
    assert "KEY=VALUE" in result.output


# ── engines test — cloud entries ──────────────────────────────────────────────

def _cloud_engine(name, provider):
    return {"name": name, "type": "cloud", "provider": provider, "api_url": ""}


def test_test_cloud_engine_key_set_exits_0(isolated_config, monkeypatch):
    """A cloud entry whose API-key env var is set is reported as OK (exit 0)."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "secret")
    _write_engines(isolated_config, _cloud_engine("dg", "deepgram"))
    result = runner.invoke(app, ["engines", "test"])
    assert result.exit_code == 0
    assert "key set" in result.output
    assert "dg" in result.output


def test_test_cloud_engine_no_key_exits_1(isolated_config, monkeypatch):
    """A cloud entry with no API-key env var is reported as not-OK (exit 1)."""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    _write_engines(isolated_config, _cloud_engine("dg", "deepgram"))
    result = runner.invoke(app, ["engines", "test"])
    assert result.exit_code == 1
    assert "no key" in result.output
    assert "dg" in result.output


def test_test_single_cloud_engine_by_name(isolated_config, monkeypatch):
    """Specifying a cloud entry by name also evaluates via is_usable."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "k")
    _write_engines(isolated_config, _cloud_engine("dg", "deepgram"))
    result = runner.invoke(app, ["engines", "test", "dg"])
    assert result.exit_code == 0
    assert "key set" in result.output


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
