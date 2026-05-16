"""Tests for the OpenAI-compatible /v1/audio/* + /v1/engines routes."""
from unittest.mock import patch

from resona_api import engine_registry as reg


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
