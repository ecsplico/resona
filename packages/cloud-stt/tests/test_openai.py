"""Tests for the OpenAI cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import openai

URL = "https://api.openai.com/v1/audio/transcriptions"

_OK_BODY = {
    "text": "hello there",
    "language": "english",
    "segments": [
        {"start": 0.0, "end": 0.6, "text": "hello"},
        {"start": 0.6, "end": 1.1, "text": "there"},
    ],
}


@respx.mock
def test_transcribe_maps_verbose_json_segments_directly(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = openai.transcribe(wav_path, api_key="oakey", language="en")
    assert route.called
    assert result["text"] == "hello there"
    assert result["language"] == "english"
    assert result["segments"] == [
        {"start": 0.0, "end": 0.6, "text": "hello"},
        {"start": 0.6, "end": 1.1, "text": "there"},
    ]


@respx.mock
def test_transcribe_sends_bearer_auth_and_multipart_fields(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    openai.transcribe(wav_path, api_key="oakey", model="whisper-1", language="de")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer oakey"
    body = req.content.decode("utf-8", errors="ignore")
    assert 'name="model"' in body
    assert "whisper-1" in body
    assert 'name="response_format"' in body
    assert "verbose_json" in body
    assert 'name="language"' in body
    assert 'name="file"' in body


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    openai.transcribe(
        wav_path,
        api_key="oakey",
        options={"temperature": 0.2, "bogus": "x"},
    )
    body = route.calls.last.request.content.decode("utf-8", errors="ignore")
    assert 'name="temperature"' in body
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_raises_provider_http_error_on_401(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(401, text="invalid key"))
    with pytest.raises(ProviderHTTPError) as exc:
        openai.transcribe(wav_path, api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "openai"
