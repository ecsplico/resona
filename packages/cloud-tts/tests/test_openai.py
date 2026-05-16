"""Tests for the OpenAI cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import ProviderHTTPError
from resona_cloud_tts.providers import openai

URL = "https://api.openai.com/v1/audio/speech"


@respx.mock
def test_synthesize_returns_audio_and_content_type(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = openai.synthesize("hallo welt", api_key="oakey")
    assert route.called
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_sends_bearer_auth_and_json_body(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    openai.synthesize(
        "hallo", api_key="oakey", model="tts-1", voice="echo",
        response_format="opus",
    )
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer oakey"
    import json
    body = json.loads(req.content)
    assert body["model"] == "tts-1"
    assert body["voice"] == "echo"
    assert body["input"] == "hallo"
    assert body["response_format"] == "opus"


@respx.mock
def test_synthesize_drops_unknown_options(fake_audio, caplog):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    openai.synthesize(
        "hi", api_key="oakey", options={"speed": 1.2, "bogus": "x"}
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["speed"] == 1.2
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_synthesize_raises_on_401():
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderHTTPError) as exc:
        openai.synthesize("hi", api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "openai"
