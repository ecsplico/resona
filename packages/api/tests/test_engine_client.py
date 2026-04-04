"""Tests for resona_api.engine_client.EngineClient using respx."""
import io
import struct
import tempfile
import wave
from pathlib import Path

import httpx
import pytest
import respx

from resona_api.engine_client import EngineClient


ENGINE_URL = "http://test-engine:7001"


def make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def engine_client():
    c = EngineClient(base_url=ENGINE_URL)
    yield c
    c.close()


@pytest.fixture
def audio_file(tmp_path):
    return make_wav(tmp_path / "audio.wav")


def test_transcribe_basic(engine_client, audio_file):
    response_body = {"text": "hello", "language": "de", "segments": []}
    with respx.mock:
        respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(200, json=response_body)
        )
        result = engine_client.transcribe(audio_file, language="de")
    assert result["text"] == "hello"
    assert result["language"] == "de"


def test_transcribe_sends_language(engine_client, audio_file):
    with respx.mock:
        route = respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(200, json={"text": "", "language": "en", "segments": []})
        )
        engine_client.transcribe(audio_file, language="en")
    request = route.calls.last.request
    body = request.content.decode('latin-1')
    assert "en" in body


def test_transcribe_does_not_send_replacements(engine_client, audio_file):
    """Verify that the engine client never sends replacements to the engine.

    Replacements are applied locally by the PostprocessPipeline in tasks_transcribe,
    NOT forwarded to the engine as form data.
    """
    with respx.mock:
        route = respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(200, json={"text": "hello", "language": "de", "segments": []})
        )
        engine_client.transcribe(audio_file, language="de")
    body = route.calls.last.request.content.decode('latin-1')
    assert "replacements" not in body


def test_transcribe_raises_on_error(engine_client, audio_file):
    with respx.mock:
        respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(httpx.HTTPStatusError):
            engine_client.transcribe(audio_file)


def test_transcribe_with_initial_prompt(engine_client, audio_file):
    with respx.mock:
        route = respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(200, json={"text": "hi", "language": "de", "segments": []})
        )
        engine_client.transcribe(audio_file, initial_prompt="my context")
    body = route.calls.last.request.content.decode('latin-1')
    assert "my context" in body


def test_transcribe_no_replacements_with_initial_prompt(engine_client, audio_file):
    """Even when initial_prompt is provided, replacements must NOT be in the request."""
    with respx.mock:
        route = respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(200, json={"text": "hi", "language": "de", "segments": []})
        )
        engine_client.transcribe(audio_file, initial_prompt="some prompt")
    body = route.calls.last.request.content.decode('latin-1')
    assert "replacements" not in body


def test_health_returns_true(engine_client):
    with respx.mock:
        respx.get(f"{ENGINE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        assert engine_client.health() is True


def test_health_returns_false_on_non_200(engine_client):
    with respx.mock:
        respx.get(f"{ENGINE_URL}/health").mock(
            return_value=httpx.Response(503)
        )
        assert engine_client.health() is False


def test_health_returns_false_on_connection_error(engine_client):
    with respx.mock:
        respx.get(f"{ENGINE_URL}/health").mock(side_effect=httpx.ConnectError("down"))
        assert engine_client.health() is False


def test_transcribe_raises_on_connection_error(engine_client, audio_file):
    """Engine unreachable — ConnectError must propagate to the caller."""
    with respx.mock:
        respx.post(f"{ENGINE_URL}/transcribe").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        with pytest.raises(httpx.ConnectError):
            engine_client.transcribe(audio_file, language="de")


def test_transcribe_raises_on_timeout(engine_client, audio_file):
    """Engine times out — ReadTimeout must propagate to the caller."""
    with respx.mock:
        respx.post(f"{ENGINE_URL}/transcribe").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        with pytest.raises(httpx.ReadTimeout):
            engine_client.transcribe(audio_file, language="de")


def test_transcribe_raises_on_non_200(engine_client, audio_file):
    """Engine returns 500 — raise_for_status must trigger HTTPStatusError."""
    with respx.mock:
        respx.post(f"{ENGINE_URL}/transcribe").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            engine_client.transcribe(audio_file, language="de")
    assert exc_info.value.response.status_code == 500


def test_health_returns_false_on_timeout(engine_client):
    """health() must not crash on ReadTimeout — return False instead."""
    with respx.mock:
        respx.get(f"{ENGINE_URL}/health").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        assert engine_client.health() is False
