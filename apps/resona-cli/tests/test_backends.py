"""Tests for resona_cli.backends CLI commands."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


# ── Helper to isolate config file ────────────────────────────────────────────

def patch_config(tmp_path):
    """Return a context manager that redirects config I/O to tmp_path."""
    config_file = tmp_path / "config.json"
    return (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
    )


# ── backends list ─────────────────────────────────────────────────────────────

def test_list_no_backends(tmp_path):
    with patch("resona_client.config.CONFIG_FILE", tmp_path / "config.json"):
        result = runner.invoke(app, ["backends", "list"])
    assert "No backends configured" in result.output


def test_list_shows_backends(tmp_path):
    from resona_client.config import BackendConfig, BackendEntry
    cfg = BackendConfig(backends=[BackendEntry(name="local", api_url="http://localhost:7000")])
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"backends": [{"name": "local", "api_url": "http://localhost:7000", "api_key": "", "compose_dir": None}]}))

    with (
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=False),
    ):
        result = runner.invoke(app, ["backends", "list"])
    assert "local" in result.output


# ── backends add ──────────────────────────────────────────────────────────────

def test_add_backend(tmp_path):
    config_file = tmp_path / "config.json"
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=False),
    ):
        result = runner.invoke(app, ["backends", "add", "myserver", "http://myserver:7000"])
    assert result.exit_code == 0
    assert "myserver" in result.output

    data = json.loads(config_file.read_text())
    assert data["backends"][0]["name"] == "myserver"


def test_add_backend_with_key(tmp_path):
    config_file = tmp_path / "config.json"
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=True),
    ):
        result = runner.invoke(app, ["backends", "add", "secure", "http://s:7000", "--key", "abc123"])
    assert result.exit_code == 0
    data = json.loads(config_file.read_text())
    assert data["backends"][0]["api_key"] == "abc123"


def test_add_backend_duplicate_fails(tmp_path):
    config_file = tmp_path / "config.json"
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=False),
    ):
        runner.invoke(app, ["backends", "add", "dup", "http://dup:7000"])
        result = runner.invoke(app, ["backends", "add", "dup", "http://dup:7001"])
    assert result.exit_code == 1


# ── backends remove ───────────────────────────────────────────────────────────

def test_remove_backend(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backends": [{"name": "todelete", "api_url": "http://x:7000", "api_key": "", "compose_dir": None}]
    }))
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
    ):
        result = runner.invoke(app, ["backends", "remove", "todelete"])
    assert result.exit_code == 0
    assert "todelete" in result.output

    data = json.loads(config_file.read_text())
    assert data["backends"] == []


def test_remove_nonexistent_backend_fails(tmp_path):
    config_file = tmp_path / "config.json"
    with (
        patch("resona_client.config.CONFIG_DIR", tmp_path),
        patch("resona_client.config.CONFIG_FILE", config_file),
    ):
        result = runner.invoke(app, ["backends", "remove", "ghost"])
    assert result.exit_code == 1


# ── backends test ─────────────────────────────────────────────────────────────

def test_test_backends_no_backends(tmp_path):
    with patch("resona_client.config.CONFIG_FILE", tmp_path / "config.json"):
        result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 1


def test_test_backends_reachable_exits_0(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backends": [{"name": "ok", "api_url": "http://ok:7000", "api_key": "", "compose_dir": None}]
    }))
    with (
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=True),
    ):
        result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 0


def test_test_backends_unreachable_exits_1(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backends": [{"name": "dead", "api_url": "http://dead:7000", "api_key": "", "compose_dir": None}]
    }))
    with (
        patch("resona_client.config.CONFIG_FILE", config_file),
        patch("resona_cli.backends.is_reachable", return_value=False),
    ):
        result = runner.invoke(app, ["backends", "test"])
    assert result.exit_code == 1


def test_test_specific_backend_not_found(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "backends": [{"name": "existing", "api_url": "http://x:7000", "api_key": "", "compose_dir": None}]
    }))
    with patch("resona_client.config.CONFIG_FILE", config_file):
        result = runner.invoke(app, ["backends", "test", "ghost"])
    assert result.exit_code == 1
