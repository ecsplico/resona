import io
import struct
from unittest.mock import patch, MagicMock

import pytest
import numpy as np
from fastapi.testclient import TestClient

from resona_asr_core.protocol import TranscriptionResult


@pytest.fixture
def mock_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="hello world",
        language="en",
        segments=[{"start": 0.0, "end": 1.0, "text": "hello world"}],
    )
    return t


@pytest.fixture
def client(mock_transcriber):
    with patch("resona_engine_server.app.get_transcriber", return_value=mock_transcriber):
        with patch("resona_engine_server.auth.config", return_value=None):
            from resona_engine_server.app import app
            yield TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_transcribe_returns_text(client, mock_transcriber):
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
            data={"language": "en"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "hello world"
    assert body["language"] == "en"
    assert "md" not in body


def test_transcribe_no_md_field(client, mock_transcriber):
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    assert "md" not in resp.json()
