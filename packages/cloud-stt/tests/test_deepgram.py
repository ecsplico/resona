"""Tests for the Deepgram cloud provider."""
import httpx
import pytest
import respx

from resona_cloud_stt.errors import ProviderHTTPError
from resona_cloud_stt.providers import deepgram

URL = "https://api.deepgram.com/v1/listen"

_OK_BODY = {
    "results": {
        "channels": [
            {
                "alternatives": [
                    {
                        "transcript": "guten morgen",
                        "words": [
                            {"word": "guten", "start": 0.1, "end": 0.5},
                            {"word": "morgen", "start": 0.6, "end": 1.2},
                        ],
                    }
                ]
            }
        ]
    }
}


@respx.mock
def test_transcribe_parses_transcript_and_segment(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = deepgram.transcribe(wav_path, api_key="dgkey", language="de")
    assert route.called
    assert result["text"] == "guten morgen"
    assert result["language"] == "de"
    assert result["segments"] == [
        {
            "start": 0.1,
            "end": 1.2,
            "text": "guten morgen",
            "words": [
                {"start": 0.1, "end": 0.5, "word": " guten"},
                {"start": 0.6, "end": 1.2, "word": " morgen"},
            ],
        }
    ]


@respx.mock
def test_transcribe_returns_utterances_with_word_speakers(wav_path):
    body = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "Hallo. Guten Tag.", "words": []}]}],
            "utterances": [
                {"start": 0.0, "end": 0.5, "transcript": "Hallo.", "speaker": 0,
                 "words": [{"word": "hallo", "punctuated_word": "Hallo.", "start": 0.0, "end": 0.5, "speaker": 0}]},
                {"start": 1.0, "end": 1.8, "transcript": "Guten Tag.", "speaker": 1,
                 "words": [
                     {"word": "guten", "punctuated_word": "Guten", "start": 1.0, "end": 1.3, "speaker": 1},
                     {"word": "tag", "punctuated_word": "Tag.", "start": 1.4, "end": 1.8, "speaker": 1},
                 ]},
            ],
        }
    }
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    result = deepgram.transcribe(wav_path, api_key="dgkey", options={"diarize": True, "utterances": True})
    params = route.calls.last.request.url.params
    assert (params["diarize"], params["utterances"]) == ("true", "true")
    assert [s["text"] for s in result["segments"]] == ["Hallo.", "Guten Tag."]
    assert result["segments"][1]["words"] == [
        {"start": 1.0, "end": 1.3, "word": " Guten", "speaker": 1},
        {"start": 1.4, "end": 1.8, "word": " Tag.", "speaker": 1},
    ]


@respx.mock
def test_transcribe_sends_token_auth_header_and_query_params(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    deepgram.transcribe(wav_path, api_key="dgkey", model="nova-2", language="en")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Token dgkey"
    assert req.url.params["model"] == "nova-2"
    assert req.url.params["language"] == "en"


@respx.mock
def test_transcribe_omits_language_when_none(wav_path):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    result = deepgram.transcribe(wav_path, api_key="dgkey")
    assert "language" not in route.calls.last.request.url.params
    assert result["language"] == ""


@respx.mock
def test_transcribe_maps_whitelisted_options_drops_unknown(wav_path, caplog):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_OK_BODY))
    deepgram.transcribe(
        wav_path,
        api_key="dgkey",
        options={"smart_format": True, "bogus": "x"},
    )
    params = route.calls.last.request.url.params
    assert params["smart_format"] == "true"
    assert "bogus" not in params
    assert "bogus" in caplog.text


@respx.mock
def test_transcribe_empty_words_yields_zero_bounds(wav_path):
    body = {
        "results": {
            "channels": [{"alternatives": [{"transcript": "hi", "words": []}]}]
        }
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    result = deepgram.transcribe(wav_path, api_key="dgkey")
    assert result["segments"] == [{"start": 0.0, "end": 0.0, "text": "hi"}]


@respx.mock
def test_transcribe_raises_provider_http_error_on_401(wav_path):
    respx.post(URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ProviderHTTPError) as exc:
        deepgram.transcribe(wav_path, api_key="bad")
    assert exc.value.status_code == 401
    assert exc.value.provider == "deepgram"
