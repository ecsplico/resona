"""Tests for resona_client.client.ResonaClient using respx."""
import io
import struct
import time
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from resona_client.client import ResonaClient

BASE = "http://localhost:7000"


def _make_wav(path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    path.write_bytes(buf.getvalue())
    return path


@pytest.fixture
def client():
    c = ResonaClient(base_url=BASE)
    yield c
    c.close()


@pytest.fixture
def audio_file(tmp_path):
    return _make_wav(tmp_path / "audio.wav")


# ── Constructor ───────────────────────────────────────────────────────────────

def test_init_reads_resona_env(monkeypatch):
    monkeypatch.delenv("WS_API_URL", raising=False)
    monkeypatch.delenv("WS_API_KEY", raising=False)
    monkeypatch.setenv("RESONA_API_URL", "http://myserver:8000")
    monkeypatch.setenv("RESONA_API_KEY", "testkey")
    c = ResonaClient()
    assert c.base_url == "http://myserver:8000"
    assert c.api_key == "testkey"
    c.close()


def test_init_falls_back_to_ws_env(monkeypatch):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.delenv("RESONA_API_KEY", raising=False)
    monkeypatch.setenv("WS_API_URL", "http://myserver:8000")
    monkeypatch.setenv("WS_API_KEY", "wskey")
    c = ResonaClient()
    assert c.base_url == "http://myserver:8000"
    assert c.api_key == "wskey"
    c.close()


def test_resona_env_takes_priority_over_ws_env(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://resona-server:9000")
    monkeypatch.setenv("WS_API_URL", "http://ws-server:8000")
    monkeypatch.setenv("RESONA_API_KEY", "resona-key")
    monkeypatch.setenv("WS_API_KEY", "ws-key")
    c = ResonaClient()
    assert c.base_url == "http://resona-server:9000"
    assert c.api_key == "resona-key"
    c.close()


def test_init_strips_trailing_slash():
    c = ResonaClient(base_url="http://server:7000/")
    assert c.base_url == "http://server:7000"
    c.close()


def test_from_config_uses_resona_env_url(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://envurl:7000")
    monkeypatch.delenv("WS_API_URL", raising=False)
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://envurl:7000"
    c.close()


def test_from_config_uses_ws_env_url_as_fallback(monkeypatch):
    monkeypatch.delenv("RESONA_API_URL", raising=False)
    monkeypatch.setenv("WS_API_URL", "http://envurl:7000")
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://envurl:7000"
    c.close()


def test_from_config_resona_env_takes_priority_over_ws_env(monkeypatch):
    monkeypatch.setenv("RESONA_API_URL", "http://resona-url:9000")
    monkeypatch.setenv("WS_API_URL", "http://ws-url:8000")
    c = ResonaClient.from_config(auto_start=False)
    assert c.base_url == "http://resona-url:9000"
    c.close()


# ── Jobs ──────────────────────────────────────────────────────────────────────

def test_submit_job(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/jobs").mock(
            return_value=httpx.Response(200, json=[{"id": 42, "result": "/job/42"}])
        )
        result = client.submit_job(audio_file)
    assert result["id"] == 42


def test_submit_job_returns_first_element(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/jobs").mock(
            return_value=httpx.Response(200, json=[{"id": 1}, {"id": 2}])
        )
        result = client.submit_job(audio_file)
    assert result["id"] == 1


def test_get_job(client):
    with respx.mock:
        respx.get(f"{BASE}/job/5").mock(
            return_value=httpx.Response(200, json={"id": 5, "status": "completed"})
        )
        result = client.get_job(5)
    assert result["status"] == "completed"


def test_get_job_raises_on_404(client):
    with respx.mock:
        respx.get(f"{BASE}/job/99").mock(return_value=httpx.Response(404))
        with pytest.raises(httpx.HTTPStatusError):
            client.get_job(99)


def test_list_jobs(client):
    with respx.mock:
        respx.get(f"{BASE}/jobs/").mock(
            return_value=httpx.Response(200, json=[{"id": 1}, {"id": 2}])
        )
        result = client.list_jobs()
    assert len(result) == 2


def test_wait_for_job_completes(client):
    responses = [
        httpx.Response(200, json={"id": 1, "status": "pending"}),
        httpx.Response(200, json={"id": 1, "status": "completed", "transcript": "hi"}),
    ]
    with respx.mock:
        respx.get(f"{BASE}/job/1").mock(side_effect=responses)
        with patch("time.sleep"):
            result = client.wait_for_job(1, poll=0.0)
    assert result["status"] == "completed"


def test_wait_for_job_failed(client):
    with respx.mock:
        respx.get(f"{BASE}/job/2").mock(
            return_value=httpx.Response(200, json={"id": 2, "status": "failed"})
        )
        result = client.wait_for_job(2, poll=0.0)
    assert result["status"] == "failed"


def test_wait_for_job_timeout(client):
    with respx.mock:
        respx.get(f"{BASE}/job/3").mock(
            return_value=httpx.Response(200, json={"id": 3, "status": "pending"})
        )
        with patch("time.sleep"):
            with pytest.raises(TimeoutError):
                client.wait_for_job(3, poll=0.0, timeout=0.001)


# ── Replacements ──────────────────────────────────────────────────────────────

def test_list_replacements(client):
    with respx.mock:
        respx.get(f"{BASE}/replacements/").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert client.list_replacements() == []


def test_add_replacement(client):
    with respx.mock:
        respx.post(f"{BASE}/replacements/").mock(
            return_value=httpx.Response(200, json={"id": 1, "name": "x", "replacement": "y"})
        )
        result = client.add_replacement("x", "y")
    assert result["id"] == 1


def test_delete_replacement(client):
    with respx.mock:
        respx.delete(f"{BASE}/replacements/1").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client.delete_replacement(1)  # should not raise


# ── Prompts ───────────────────────────────────────────────────────────────────

def test_list_prompts(client):
    with respx.mock:
        respx.get(f"{BASE}/prompts/").mock(
            return_value=httpx.Response(200, json=[])
        )
        assert client.list_prompts() == []


def test_add_prompt(client):
    with respx.mock:
        respx.post(f"{BASE}/prompts/").mock(
            return_value=httpx.Response(200, json={"id": 3, "phrase": "test phrase"})
        )
        result = client.add_prompt("test phrase")
    assert result["phrase"] == "test phrase"


def test_activate_prompt(client):
    with respx.mock:
        respx.put(f"{BASE}/prompts/2/activate").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client.activate_prompt(2)  # should not raise


def test_deactivate_prompt(client):
    with respx.mock:
        respx.put(f"{BASE}/prompts/2/deactivate").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client.deactivate_prompt(2)


def test_remove_prompt(client):
    with respx.mock:
        respx.delete(f"{BASE}/prompts/2").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client.remove_prompt(2)


# ── Context manager ───────────────────────────────────────────────────────────

def test_context_manager():
    with ResonaClient(base_url=BASE) as c:
        assert c.base_url == BASE


# ── v1 Audio & Engine routes ──────────────────────────────────────────────────

def test_list_engines(client):
    with respx.mock:
        respx.get(f"{BASE}/v1/engines").mock(
            return_value=httpx.Response(200, json={
                "engines": [{"name": "faster-whisper", "kind": "local",
                              "capabilities": ["stt"], "private": True,
                              "available": True, "models": ["large-v3"],
                              "url": "http://localhost:7001", "provider": None}],
                "default": "faster-whisper",
            })
        )
        result = client.list_engines()
    assert "engines" in result
    assert result["engines"][0]["name"] == "faster-whisper"


def test_create_transcription(client, audio_file):
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/transcriptions").mock(
            return_value=httpx.Response(200, json={
                "text": "hello world", "language": "de", "segments": []
            })
        )
        result = client.create_transcription(audio_file, language="de")
    assert result["text"] == "hello world"


def test_create_speech(client):
    fake_audio = b"\xff\xfb\x90\x00" * 100  # fake mp3 bytes
    with respx.mock:
        respx.post(f"{BASE}/v1/audio/speech").mock(
            return_value=httpx.Response(200, content=fake_audio,
                                        headers={"content-type": "audio/mpeg"})
        )
        result = client.create_speech("hello", voice="nova")
    assert isinstance(result, bytes)
    assert len(result) > 0
