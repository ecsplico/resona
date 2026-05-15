"""Verify lazy-loaded TUI commands give helpful errors when their extras aren't installed."""
import pytest
from typer.testing import CliRunner

from resona_cli.main import app

runner = CliRunner()


def test_rec_without_record_extra_shows_install_hint(monkeypatch):
    """Running `resona rec` without textual/sounddevice gives a clear install hint."""
    monkeypatch.setattr(
        "resona_cli.main._check_missing",
        lambda modules: ["textual", "sounddevice", "soundfile"],
    )
    result = runner.invoke(app, ["rec"])
    assert result.exit_code != 0
    assert "uv tool install" in result.output.lower() or "pip install" in result.output.lower()
    assert "[record]" in result.output


def test_in_process_engine_without_engine_extra_shows_hint(monkeypatch):
    """Constructing InProcessEngine without resona-asr-core gives an install hint."""
    from resona_cli.engine import InProcessEngine

    def fake_import(*args, **kwargs):
        raise ImportError("No module named 'resona_asr_core'")

    monkeypatch.setattr("resona_cli.engine._import_asr_core", fake_import)

    with pytest.raises(ImportError, match=r"resona-cli\["):
        InProcessEngine(engine="faster-whisper")
