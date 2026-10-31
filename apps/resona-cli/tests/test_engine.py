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
    """RemoteEngine.transcribe submits an audio job, waits, and returns the result Markdown."""
    from resona_client.client import AudioOptions, JobRef, JobView
    from resona_job_core import JobResult

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.submit_job.return_value = JobRef(job_id=7, status="pending")
    mock_client.wait_for_job.return_value = JobView.from_dict(
        {"job_id": 7, "kind": "audio", "status": "completed", "private": True})
    mock_client.get_result.return_value = JobResult(
        kind="audio", raw_text="hi", markdown="Hi.", language="de",
        segments=[{"start": 0.0, "end": 1.0, "text": "hi"}], provenance={},
    )

    with patch("resona_cli.engine.ResonaClient", return_value=mock_client):
        engine = RemoteEngine(base_url="http://x:7000")
        result = engine.transcribe(audio, language="de", translate=True)

    assert result["text"] == "Hi."
    assert result["language"] == "de"
    assert result["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    kwargs = mock_client.submit_job.call_args.kwargs
    assert kwargs["kind"] == "audio"
    assert kwargs["options"] == AudioOptions(translate=True)
    assert kwargs["private"] is True
    mock_client.wait_for_job.assert_called_once_with(7, timeout=None)
    mock_client.get_result.assert_called_once_with(7)


def test_remote_engine_raises_on_failed_job(tmp_path):
    from resona_client.client import JobRef, JobView

    mock_client = MagicMock()
    mock_client.submit_job.return_value = JobRef(job_id=8, status="pending")
    mock_client.wait_for_job.return_value = JobView.from_dict({
        "job_id": 8, "kind": "audio", "status": "failed", "private": True,
        "error": {"code": "unsupported_media", "message": "bad input"},
    })

    with patch("resona_cli.engine.ResonaClient", return_value=mock_client):
        engine = RemoteEngine(base_url="http://x:7000")
        with pytest.raises(RuntimeError, match="bad input"):
            engine.transcribe(tmp_path / "a.wav")
    mock_client.get_result.assert_not_called()


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
