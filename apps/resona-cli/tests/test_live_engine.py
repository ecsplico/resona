"""Tests for `resona live` engine resolution (_resolve_live_engine)."""
import os

import pytest
import typer

import resona_asr_core.registry as reg
from resona_cli.main import _resolve_live_engine


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RESONA_ENGINE", raising=False)


def test_explicit_engine_when_installed(monkeypatch):
    monkeypatch.setattr(reg, "list_engine_names", lambda: ["faster-whisper", "mlx-whisper"])
    assert _resolve_live_engine("mlx-whisper") == "mlx-whisper"
    assert os.environ["RESONA_ENGINE"] == "mlx-whisper"


def test_defaults_to_platform_preferred(monkeypatch):
    monkeypatch.setattr(reg, "list_engine_names", lambda: ["faster-whisper", "mlx-whisper"])
    monkeypatch.setattr(reg, "platform_preferred_engine", lambda: "mlx-whisper")
    assert _resolve_live_engine(None) == "mlx-whisper"


def test_respects_existing_env(monkeypatch):
    monkeypatch.setattr(reg, "list_engine_names", lambda: ["faster-whisper", "whisper"])
    monkeypatch.setenv("RESONA_ENGINE", "whisper")
    assert _resolve_live_engine(None) == "whisper"


def test_uninstalled_engine_exits(monkeypatch):
    monkeypatch.setattr(reg, "list_engine_names", lambda: ["faster-whisper"])
    with pytest.raises(typer.Exit):
        _resolve_live_engine("mlx-whisper")
