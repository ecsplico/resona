"""Tests for the resona-api engine registry."""
import httpx
import respx

from resona_api import engine_registry as reg


@respx.mock
def test_build_catalogue_probes_local_and_lists_cloud(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://eng-a:7001")
    monkeypatch.setenv("OPENAI_API_KEY", "set")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    respx.get("http://eng-a:7001/health").mock(
        return_value=httpx.Response(
            200, json={"status": "ok", "engine": "faster-whisper",
                       "models": ["large-v3"]}
        )
    )
    catalogue = reg.build_catalogue()
    by_name = {e.name: e for e in catalogue}

    assert by_name["faster-whisper"].kind == "local"
    assert by_name["faster-whisper"].private is True
    assert by_name["faster-whisper"].available is True
    assert by_name["faster-whisper"].capabilities == ["stt"]

    assert by_name["openai"].kind == "cloud"
    assert by_name["openai"].private is False
    assert by_name["openai"].available is True
    assert "tts" in by_name["openai"].capabilities

    assert by_name["deepgram"].available is False


@respx.mock
def test_unreachable_local_is_listed_unavailable(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://dead:7001")
    respx.get("http://dead:7001/health").mock(side_effect=httpx.ConnectError("x"))
    catalogue = reg.build_catalogue()
    local = [e for e in catalogue if e.kind == "local"]
    assert len(local) == 1
    assert local[0].available is False


@respx.mock
def test_duplicate_local_engine_name_is_deduped(monkeypatch):
    monkeypatch.setenv("RESONA_ENGINE_URLS", "http://a:7001,http://b:7001")
    body = {"status": "ok", "engine": "whisper", "models": ["large-v3"]}
    respx.get("http://a:7001/health").mock(return_value=httpx.Response(200, json=body))
    respx.get("http://b:7001/health").mock(return_value=httpx.Response(200, json=body))
    catalogue = reg.build_catalogue()
    local = [e for e in catalogue if e.kind == "local"]
    assert len(local) == 1
