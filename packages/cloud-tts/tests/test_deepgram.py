"""Tests for the Deepgram cloud TTS provider."""
import httpx
import pytest
import respx

from resona_cloud_tts.errors import ProviderHTTPError
from resona_cloud_tts.providers import deepgram

URL = "https://api.deepgram.com/v1/speak"


@respx.mock
def test_synthesize_sends_token_auth_and_text_body(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    result = deepgram.synthesize("hallo", api_key="dgkey")
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Token dgkey"
    import json
    assert json.loads(req.content)["text"] == "hallo"
    assert result["audio"] == fake_audio
    assert result["content_type"] == "audio/mpeg"


@respx.mock
def test_synthesize_passes_model_and_encoding_params(fake_audio):
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fake_audio)
    )
    deepgram.synthesize(
        "hi", api_key="dgkey", model="aura-2-orpheus-en",
        response_format="opus",
    )
    params = route.calls.last.request.url.params
    assert params["model"] == "aura-2-orpheus-en"
    assert params["encoding"] == "opus"


@respx.mock
def test_synthesize_raises_on_400():
    respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(ProviderHTTPError) as exc:
        deepgram.synthesize("hi", api_key="dgkey")
    assert exc.value.provider == "deepgram"
    assert exc.value.status_code == 400
