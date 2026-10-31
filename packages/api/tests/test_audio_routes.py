"""Tests for the OpenAI-compatible /v1/audio/* + /v1/engines routes."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from resona_api import engine_registry as reg


@pytest.fixture
def fresh_client(test_app, fresh_org, client_headers):
    """Client authenticated as a brand-new organization with default settings."""
    with TestClient(test_app, headers=client_headers(fresh_org)) as c:
        yield c


def _catalogue():
    return [
        reg.EngineInfo("faster-whisper", "local", ["stt"], True, True, ["large-v3"]),
        reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                       ["nova-3"], provider="deepgram"),
    ]


def test_list_engines_returns_catalogue(client):
    with patch.object(reg, "get_catalogue", return_value=_catalogue()):
        resp = client.get("/v1/engines")
    assert resp.status_code == 200
    body = resp.json()
    names = {e["name"] for e in body["engines"]}
    assert names == {"faster-whisper", "deepgram"}
    fw = next(e for e in body["engines"] if e["name"] == "faster-whisper")
    assert fw["private"] is True
    assert fw["kind"] == "local"
    assert "url" not in fw
    assert body["default"] == "faster-whisper"


def test_transcription_json_format(client, wav_bytes):
    info = _catalogue()[0]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_stt",
                       return_value={"text": "hallo welt", "language": "de",
                                     "segments": []}):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"response_format": "json"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"text": "hallo welt"}


def test_transcription_text_format(client, wav_bytes):
    info = _catalogue()[0]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_stt",
                       return_value={"text": "nur text", "language": "de",
                                     "segments": []}):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"response_format": "text"},
        )
    assert resp.status_code == 200
    assert resp.text == "nur text"


def test_transcription_private_cloud_engine_is_policy_denied(fresh_client, wav_bytes):
    # Cloud ASR is not an approved endpoint of this org, so admission denies it
    # before the provider is ever called.
    cloud = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=cloud), \
         patch.object(reg, "run_stt") as run_stt:
        resp = fresh_client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"engine": "deepgram", "private": "true"},
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "policy_denied"
    run_stt.assert_not_called()


def test_transcription_unknown_engine(client, wav_bytes):
    with patch.object(reg, "resolve",
                      side_effect=reg.EngineNotFoundError("unknown engine 'x'")):
        resp = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"engine": "x"},
        )
    assert resp.status_code == 400


def test_speech_returns_audio_for_cloud_enabled_org(fresh_client, fresh_org, store_settings):
    store_settings(fresh_org, cloud_enabled=True)
    info = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_tts",
                       return_value={"audio": b"mp3bytes",
                                     "content_type": "audio/mpeg"}):
        resp = fresh_client.post(
            "/v1/audio/speech",
            json={"input": "hallo welt", "engine": "deepgram", "private": False},
        )
    assert resp.status_code == 200
    assert resp.content == b"mp3bytes"
    assert resp.headers["content-type"] == "audio/mpeg"


def test_speech_cloud_is_denied_for_private_default(fresh_client):
    info = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_tts") as run_tts:
        resp = fresh_client.post(
            "/v1/audio/speech",
            json={"input": "hallo welt", "engine": "deepgram"},
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "policy_denied"
    run_tts.assert_not_called()


def test_speech_private_yields_409(client):
    err = reg.NoEngineError("no private engine available for tts")
    with patch.object(reg, "resolve", side_effect=err):
        resp = client.post(
            "/v1/audio/speech",
            json={"input": "geheim", "private": True},
        )
    assert resp.status_code == 409


def test_transcription_applies_stored_profile(fresh_client, fresh_org, store_profile, wav_bytes):
    store_profile(fresh_org, "komma", {
        "name": "komma",
        "steps": [{"type": "replacements",
                   "rules": [{"pattern": "Komma", "replacement": ","}]}],
    })
    info = _catalogue()[0]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_stt",
                       return_value={"text": "a Komma b", "language": "de",
                                     "segments": []}):
        resp = fresh_client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"profile": "komma"},
        )
    assert resp.status_code == 200
    assert "," in resp.json()["text"]


def test_transcription_rejects_inline_profile(fresh_client, wav_bytes):
    inline = ('{"name":"t","steps":[{"type":"replacements",'
              '"rules":[{"pattern":"Komma","replacement":","}]}]}')
    with patch.object(reg, "run_stt") as run_stt:
        resp = fresh_client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data={"profile": inline},
        )
    assert resp.status_code == 422
    run_stt.assert_not_called()
