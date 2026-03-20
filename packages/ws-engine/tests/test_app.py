"""Tests for ws_engine FastAPI application endpoints."""
import io
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from ws_engine.app import app

client = TestClient(app, raise_server_exceptions=True)

ASR_RESULT = {
    "text": "Hello world",
    "language": "de",
    "segments": [{"start": 0.0, "end": 1.5, "text": "Hello world"}],
}


def _wav_bytes() -> bytes:
    """Minimal valid WAV file bytes (silence, 16kHz mono)."""
    import wave, struct
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    return buf.getvalue()


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_basic(mock_asr):
    wav = _wav_bytes()
    resp = client.post(
        "/transcribe",
        files={"audio_file": ("test.wav", wav, "audio/wav")},
        data={"language": "de", "task": "transcribe"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Hello world"
    assert body["language"] == "de"
    assert "segments" in body
    assert "md" not in body  # no replacements supplied


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_with_replacements(mock_asr):
    replacements = json.dumps([{"name": "world", "replacement": "Whisper"}])
    wav = _wav_bytes()
    resp = client.post(
        "/transcribe",
        files={"audio_file": ("test.wav", wav, "audio/wav")},
        data={"language": "de", "replacements": replacements},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "md" in body
    assert body["md"] == "Hello Whisper"
    assert body["text"] == "Hello world"  # raw text unchanged


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_invalid_replacements_json(mock_asr):
    wav = _wav_bytes()
    resp = client.post(
        "/transcribe",
        files={"audio_file": ("test.wav", wav, "audio/wav")},
        data={"replacements": "not-json"},
    )
    assert resp.status_code == 200
    assert "md" not in resp.json()


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_passes_initial_prompt(mock_asr):
    wav = _wav_bytes()
    client.post(
        "/transcribe",
        files={"audio_file": ("test.wav", wav, "audio/wav")},
        data={"initial_prompt": "some context"},
    )
    _, kwargs = mock_asr.call_args
    assert kwargs.get("initial_prompt") == "some context"


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_auth_required(mock_asr):
    wav = _wav_bytes()
    with patch("ws_engine.auth.get_api_key", return_value="mykey"):
        # No header → 401
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", wav, "audio/wav")},
        )
    assert resp.status_code == 401


@patch("ws_engine.app.run_asr", return_value=ASR_RESULT)
def test_transcribe_auth_correct_key(mock_asr):
    wav = _wav_bytes()
    with patch("ws_engine.auth.get_api_key", return_value="mykey"):
        resp = client.post(
            "/transcribe",
            files={"audio_file": ("test.wav", wav, "audio/wav")},
            headers={"X-API-Key": "mykey"},
        )
    assert resp.status_code == 200
