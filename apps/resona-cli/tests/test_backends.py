"""Tests for resona_cli.backends CLI commands."""
import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


@pytest.fixture
def isolated_config(tmp_path):
    """Redirect all backend-config I/O to tmp_path.

    This patches the legacy paths too: ``BackendConfig.load()`` migrates
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


def _write_backends(config_file, *entries):
    """Seed the config file with the given backend dicts."""
    config_file.write_text(json.dumps({"backends": list(entries)}))


def _backend(name, api_url, api_key="", compose_dir=None):
    return {"name": name, "api_url": api_url, "api_key": api_key, "compose_dir": compose_dir}


# ── backends list ─────────────────────────────────────────────────────────────

def test_list_no_backends(isolated_config):
    result = runner.invoke(app, ["backends", "list"])
    assert "No backends configured" in result.output


def test_list_shows_backends(isolated_config):
    _write_backends(isolated_config, _backend("local", "http://localhost:7000"))
    with patch("resona_cli.backends.is_reachable", return_value=False):
        result = runner.invoke(app, ["backends", "list"])
    assert "local" in result.output


# ── backends add ──────────────────────────────────────────────────────────────

def test_add_backend(isolated_config):
    with patch("resona_cli.backends.is_reachable", return_value=False):
        result = runner.invoke(app, ["backends", "add", "myserver", "http://myserver:7000"])
    assert result.exit_code == 0
    assert "myserver" in result.output

    data = json.loads(isolated_config.read_text())
    assert data["backends"][0]["name"] == "myserver"


def test_add_backend_with_key(isolated_config):
    with patch("resona_cli.backends.is_reachable", return_value=True):
        result = runner.invoke(app, ["backends", "add", "secure", "http://s:7000", "--key", "abc123"])
    assert result.exit_code == 0
    data = json.loads(isolated_config.read_text())
    assert data["backends"][0]["api_key"] == "abc123"


def test_add_backend_duplicate_fails(isolated_config):
    with patch("resona_cli.backends.is_reachable", return_value=False):
        runner.invoke(app, ["backends", "add", "dup", "http://dup:7000"])
        result = runner.invoke(app, ["backends", "add", "dup", "http://dup:7001"])
    assert result.exit_code == 1


# ── backends remove ───────────────────────────────────────────────────────────

def test_remove_backend(isolated_config):
    _write_backends(isolated_config, _backend("todelete", "http://x:7000"))
    result = runner.invoke(app, ["backends", "remove", "todelete"])
    assert result.exit_code == 0
    assert "todelete" in result.output

    data = json.loads(isolated_config.read_text())
    assert data["backends"] == []


def test_remove_nonexistent_backend_fails(isolated_config):
    result = runner.invoke(app, ["backends", "remove", "ghost"])
    assert result.exit_code == 1


# ── backends test ─────────────────────────────────────────────────────────────

def test_test_backends_no_backends(isolated_config):
    result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 1


def test_test_backends_reachable_exits_0(isolated_config):
    _write_backends(isolated_config, _backend("ok", "http://ok:7000"))
    with patch("resona_cli.backends.is_reachable", return_value=True):
        result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 0


def test_test_backends_unreachable_exits_1(isolated_config):
    _write_backends(isolated_config, _backend("dead", "http://dead:7000"))
    with patch("resona_cli.backends.is_reachable", return_value=False):
        result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 1


def test_test_specific_backend_not_found(isolated_config):
    _write_backends(isolated_config, _backend("existing", "http://x:7000"))
    result = runner.invoke(app, ["backends", "test", "ghost"])
    assert result.exit_code == 1
