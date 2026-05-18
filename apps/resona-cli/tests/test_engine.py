"""Tests for the Engine abstraction used by `resona transcribe` local mode."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from resona_cli.engine import Engine, RemoteEngine


def test_remote_engine_satisfies_protocol():
    """RemoteEngine implements the Engine Protocol."""
    e = RemoteEngine(base_url="http://x:7000")
    assert isinstance(e, Engine)


def test_remote_engine_delegates_to_resona_client(tmp_path):
    """RemoteEngine.transcribe submits a job and waits for the result via ResonaClient."""
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.submit_job.return_value = {"id": 7}
    mock_client.wait_for_job.return_value = {
        "status": "completed", "transcript": "hi", "md": "", "language": "de",
    }

    with patch("resona_cli.engine.ResonaClient", return_value=mock_client):
        engine = RemoteEngine(base_url="http://x:7000")
        result = engine.transcribe(audio, language="de")

    assert result["text"] == "hi"
    assert result["language"] == "de"
    mock_client.submit_job.assert_called_once_with(audio)
    mock_client.wait_for_job.assert_called_once_with(7)


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


def test_load_audio_passes_file_object_not_path(tmp_path):
    """_load_audio opens the file and hands a file object to asr-core's load_audio.

    Regression: it previously passed str(path); load_audio calls .read() on its
    argument, which raises AttributeError on a str.
    """
    from resona_cli.engine import _load_audio

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake-audio-bytes")

    received = {}

    def fake_load_audio(file, *args, **kwargs):
        received["arg"] = file
        received["data"] = file.read()  # AttributeError if file is a str
        return np.zeros(16000, dtype=np.float32)

    with patch(
        "resona_cli.engine._import_asr_core",
        return_value=(MagicMock(), fake_load_audio),
    ):
        _load_audio(audio)

    assert hasattr(received["arg"], "read")
    assert received["data"] == b"fake-audio-bytes"


def test_in_process_engine_missing_extra_gives_install_hint(monkeypatch):
    """If resona-asr-core isn't installed, InProcessEngine raises ImportError with hint."""
    from resona_cli.engine import InProcessEngine

    def fake_import(*args, **kwargs):
        raise ImportError("no asr-core")

    monkeypatch.setattr("resona_cli.engine._import_asr_core", fake_import)
    with pytest.raises(ImportError, match=r"resona-cli\["):
        InProcessEngine(engine="faster-whisper")
