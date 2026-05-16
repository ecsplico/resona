"""Tests for the ElevenLabs cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import CloudTTSError, ProviderHTTPError
from resona_cloud_tts.providers import elevenlabs

VOICE_ID = "21m00Tcm4TlvDq8ikWAM"
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"


@respx.mock
def test_synthesize_posts_to_voice_path_with_api_key(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = elevenlabs.synthesize("hallo", api_key="elkey")
    assert route.called
    req = route.calls.last.request
    assert req.headers["xi-api-key"] == "elkey"
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_uses_explicit_voice_and_model(fake_audio):
    voice = "customVoice123"
    route = respx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
    ).mock(return_value=httpx.Response(200, content=fake_audio))
    elevenlabs.synthesize(
        "hallo", api_key="elkey", voice=voice, model="eleven_turbo_v2"
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["text"] == "hallo"
    assert body["model_id"] == "eleven_turbo_v2"


@respx.mock
def test_synthesize_folds_voice_settings_options(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    elevenlabs.synthesize(
        "hi", api_key="elkey",
        options={"stability": 0.7, "bogus": 1},
    )
    import json
    body = json.loads(route.calls.last.request.content)
    assert body["voice_settings"]["stability"] == 0.7
    assert "bogus" not in str(body)


def test_synthesize_rejects_unsupported_format():
    with pytest.raises(CloudTTSError):
        elevenlabs.synthesize("hi", api_key="elkey", response_format="flac")


@respx.mock
def test_synthesize_raises_on_401():
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(ProviderHTTPError) as exc:
        elevenlabs.synthesize("hi", api_key="bad")
    assert exc.value.provider == "elevenlabs"
