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


def _cat():
    """A fixed catalogue for resolve() tests."""
    return [
        reg.EngineInfo("faster-whisper", "local", ["stt"], True, True, ["large-v3"]),
        reg.EngineInfo("whisper", "local", ["stt"], True, False, []),
        reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                       ["nova-3"], provider="deepgram"),
    ]


def test_resolve_explicit_engine():
    info = reg.resolve("deepgram", "stt", False, catalogue=_cat())
    assert info.name == "deepgram"


def test_resolve_unknown_engine_raises():
    import pytest
    with pytest.raises(reg.EngineNotFoundError):
        reg.resolve("nope", "stt", False, catalogue=_cat())


def test_resolve_unavailable_engine_raises():
    import pytest
    with pytest.raises(reg.EngineUnavailableError):
        reg.resolve("whisper", "stt", False, catalogue=_cat())


def test_resolve_capability_mismatch_raises():
    import pytest
    with pytest.raises(reg.CapabilityError):
        reg.resolve("faster-whisper", "tts", False, catalogue=_cat())


def test_resolve_private_refuses_cloud_engine():
    import pytest
    with pytest.raises(reg.PrivacyViolationError):
        reg.resolve("deepgram", "stt", True, catalogue=_cat())


def test_resolve_default_prefers_local(monkeypatch):
    monkeypatch.delenv("RESONA_DEFAULT_ENGINE", raising=False)
    info = reg.resolve(None, "stt", False, catalogue=_cat())
    assert info.name == "faster-whisper"


def test_resolve_default_honours_env(monkeypatch):
    monkeypatch.setenv("RESONA_DEFAULT_ENGINE", "deepgram")
    info = reg.resolve(None, "stt", False, catalogue=_cat())
    assert info.name == "deepgram"


def test_resolve_no_private_engine_for_tts_raises():
    import pytest
    with pytest.raises(reg.NoEngineError):
        reg.resolve(None, "tts", True, catalogue=_cat())
