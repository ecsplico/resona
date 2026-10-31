"""Tests for the in-process Engine used by the `live` TUI."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from resona_cli.engine import InProcessEngine


def test_in_process_engine_calls_registry(tmp_path):
    """InProcessEngine.transcribe loads an engine via get_transcriber and calls it."""
    from resona_cli.engine import InProcessEngine

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = {
        "text": "hello", "language": "de", "segments": [],
    }

    with (
        patch("resona_cli.engine._load_audio", return_value=np.zeros(16000, dtype=np.float32)),
        patch("resona_cli.engine.get_transcriber", return_value=mock_transcriber),
    ):
        engine = InProcessEngine(engine="faster-whisper")
        result = engine.transcribe(audio, language="de")

    assert result["text"] == "hello"
    mock_transcriber.transcribe.assert_called_once()


def test_load_audio_passes_path_to_loader(tmp_path):
    """_load_audio hands the file path to asr-core's path-based loader.

    Path-based decoding gives ffmpeg a seekable input so non-faststart MP4/M4A
    files (e.g. iOS Voice Memos) decode correctly instead of yielding 0 samples.
    """
    from resona_cli.engine import _load_audio

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake-audio-bytes")

    received = {}

    def fake_load_audio_path(path, *args, **kwargs):
        received["arg"] = path
        return np.zeros(16000, dtype=np.float32)

    with patch(
        "resona_cli.engine._import_asr_core",
        return_value=(MagicMock(), fake_load_audio_path),
    ):
        _load_audio(audio)

    assert received["arg"] == audio


def test_in_process_engine_missing_extra_gives_install_hint(monkeypatch):
    """If resona-asr-core isn't installed, InProcessEngine raises ImportError with hint."""
    from resona_cli.engine import InProcessEngine

    def fake_import(*args, **kwargs):
        raise ImportError("no asr-core")

    monkeypatch.setattr("resona_cli.engine._import_asr_core", fake_import)
    with pytest.raises(ImportError, match=r"resona-cli\["):
        InProcessEngine(engine="faster-whisper")
