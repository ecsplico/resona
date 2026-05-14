"""Tests for the Engine abstraction used by `resona transcribe` local mode."""
from pathlib import Path
from unittest.mock import MagicMock, patch

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
