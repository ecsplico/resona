"""Contract tests — engine returns raw text, no postprocessing."""
import io
import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from resona_asr_core.protocol import TranscriptionResult


@pytest.fixture
def mock_transcriber():
    t = MagicMock()
    t.transcribe.return_value = TranscriptionResult(
        text="raw transcript",
        language="de",
        segments=[{"start": 0.0, "end": 1.0, "text": "raw transcript"}],
    )
    return t


@pytest.fixture
def client(mock_transcriber):
    with patch("resona_engine_server.app.get_transcriber", return_value=mock_transcriber):
        with patch("resona_engine_server.auth.config", return_value=None):
            from resona_engine_server.app import app
            yield TestClient(app)


def test_transcribe_response_has_no_md_field(client, mock_transcriber):
    """The engine must never return an 'md' field — postprocessing is caller-side."""
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    body = resp.json()
    assert "md" not in body
    assert "text" in body
    assert body["text"] == "raw transcript"


def test_transcribe_response_has_no_replacements_field(client, mock_transcriber):
    """Engine response must not contain replacements-related fields."""
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    body = resp.json()
    assert "replacements" not in body


def test_transcribe_endpoint_does_not_accept_replacements_form_field(client, mock_transcriber):
    """The engine /transcribe endpoint should ignore any replacements field sent to it."""
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
            data={"replacements": '[{"name": "foo", "replacement": "bar"}]'},
        )
    # Should succeed (extra form fields are ignored by FastAPI) but response has no md
    assert resp.status_code == 200
    body = resp.json()
    assert "md" not in body
    # The transcriber should NOT have received replacements
    call_kwargs = mock_transcriber.transcribe.call_args
    # Check neither args nor kwargs contain "replacements"
    if call_kwargs.kwargs:
        assert "replacements" not in call_kwargs.kwargs


def test_transcribe_returns_required_fields(client, mock_transcriber):
    """Engine must return text, language, and segments."""
    with patch("resona_engine_server.app.load_audio", return_value=np.zeros(16000)):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", io.BytesIO(b"\x00" * 100), "audio/wav")},
        )
    body = resp.json()
    assert set(body.keys()) == {"text", "language", "segments"}
