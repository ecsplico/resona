"""Verify lazy-loaded TUI commands give helpful errors when their extras aren't installed."""
import sys
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


def test_rec_without_record_extra_shows_install_hint(monkeypatch):
    """Running `resona rec` without textual/sounddevice gives a clear install hint."""
    real_import = __import__

    def hide_textual(name, *args, **kwargs):
        if name == "textual" or name.startswith("textual.") or name == "sounddevice":
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    for mod in list(sys.modules):
        if mod.startswith("resona_cli.micrec") or mod == "textual" or mod == "sounddevice":
            sys.modules.pop(mod, None)

    monkeypatch.setattr("builtins.__import__", hide_textual)
    result = runner.invoke(app, ["rec"])
    assert result.exit_code != 0
    assert "pip install" in result.output.lower() or "uv tool install" in result.output.lower()
    assert "[record]" in result.output or "record" in result.output.lower()
