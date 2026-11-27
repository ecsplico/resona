"""Tests for the ElevenLabs cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import elevenlabs

URL = "https://api.elevenlabs.io/v1/speech-to-text"

_OK_BODY = {
    "text": "bonjour le monde",
    "language_code": "fr",
    "words": [
        {"text": "bonjour", "start": 0.2, "end": 0.7},
        {"text": "monde", "start": 0.8, "end": 1.5},
    ],
}


@respx.mock
def test_transcribe_parses_text_language_segment(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = elevenlabs.transcribe(wav_path, api_key="elkey", language="fr")
    assert route.called
    assert result["text"] == "bonjour le monde"
    assert result["language"] == "fr"
    assert result["segments"] == [{"start": 0.2, "end": 1.5, "text": "bonjour le monde"}]


@respx.mock
def test_transcribe_sends_xi_api_key_and_multipart_fields(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey", model="scribe_v2", language="de")
    req = route.calls.last.request
    assert req.headers["xi-api-key"] == "elkey"
    body = req.content.decode("utf-8", errors="ignore")
    assert "scribe_v2" in body
    assert 'name="model_id"' in body
    assert 'name="language_code"' in body
    assert "de" in body
    assert 'name="file"' in body


@respx.mock
def test_transcribe_defaults_model_id_to_scribe_v1(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey")
    assert "scribe_v1" in route.calls.last.request.content.decode("utf-8", errors="ignore")


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(
        wav_path,
        api_key="elkey",
        options={"diarize": True, "bogus": "x"},
    )
    body = route.calls.last.request.content.decode("utf-8", errors="ignore")
    assert 'name="diarize"' in body
    assert "bogus" not in body
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_raises_provider_http_error_on_400(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(ProviderHTTPError) as exc:
        elevenlabs.transcribe(wav_path, api_key="elkey")
    assert exc.value.status_code == 400
    assert exc.value.provider == "elevenlabs"


def _form_fields(request) -> list[tuple[str, str]]:
    """(name, value) of every non-file multipart field, in order."""
    import re

    body = request.content.decode("utf-8", errors="ignore")
    return re.findall(r'name="([^"]+)"\r\n\r\n(.*?)\r\n', body)


@respx.mock
def test_transcribe_sends_keyterms_as_repeated_form_fields(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey", model="scribe_v2", keyterms=["Monic", "Quetiapin", "MONIC"])
    fields = _form_fields(route.calls.last.request)
    assert [v for k, v in fields if k == "keyterms"] == ["Monic", "Quetiapin"]
    assert ("model_id", "scribe_v2") in fields


@respx.mock
def test_scribe_v1_gets_no_keyterms(wav_path):
    # Scribe v1 has no keyterm prompting; sending the field would be rejected.
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    elevenlabs.transcribe(wav_path, api_key="elkey", model="scribe_v1", keyterms=["Monic"])
    assert "keyterms" not in dict(_form_fields(route.calls.last.request))
