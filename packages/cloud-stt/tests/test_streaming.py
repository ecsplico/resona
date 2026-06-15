"""Tests for live streaming sessions (Deepgram / ElevenLabs) and the dispatcher.

The provider WebSocket is faked via ``websockets.connect``; async coroutines are
driven with ``asyncio.run`` so no pytest-asyncio plugin is required.
"""
import asyncio
import base64
import json

import pytest

from resona_cloud_stt.streaming import StreamTranscript, open_stream, supports_streaming


class _FakeProviderWS:
    """Fake websockets client: records sends, async-iterates scripted messages."""

    def __init__(self, incoming=()):
        self.sent = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for msg in self._incoming:
            yield msg


async def _collect(session):
    return [(t.text, t.is_final) async for t in session]


# ── Dispatcher ───────────────────────────────────────────────────────

def test_supports_streaming():
    assert supports_streaming("deepgram")
    assert supports_streaming("elevenlabs")
    assert not supports_streaming("openai")


def test_open_stream_rejects_non_streaming():
    with pytest.raises(ValueError):
        asyncio.run(open_stream("openai", api_key="k"))


# ── Deepgram ─────────────────────────────────────────────────────────

def test_deepgram_open_stream_url_and_headers(monkeypatch):
    import resona_cloud_stt.providers.deepgram as dg
    ws = _FakeProviderWS()
    captured = {}

    async def fake_connect(url, **kw):
        captured["url"] = url
        captured["kw"] = kw
        return ws

    monkeypatch.setattr("websockets.connect", fake_connect)
    asyncio.run(dg.open_stream(
        api_key="k", model="nova-3", language="de",
        sample_rate=16000, interim_results=True,
    ))
    assert captured["url"].startswith("wss://api.deepgram.com/v1/listen?")
    assert "encoding=linear16" in captured["url"]
    assert "sample_rate=16000" in captured["url"]
    assert "interim_results=true" in captured["url"]
    assert "language=de" in captured["url"]
    assert captured["kw"]["additional_headers"]["Authorization"] == "Token k"


def test_deepgram_results_parsing():
    ws = _FakeProviderWS([
        json.dumps({"type": "Metadata"}),
        json.dumps({"type": "Results", "is_final": False,
                    "channel": {"alternatives": [{"transcript": "guten"}]}}),
        json.dumps({"type": "Results", "is_final": True,
                    "channel": {"alternatives": [{"transcript": "guten tag"}]}}),
    ])
    from resona_cloud_stt.providers.deepgram import _DeepgramStream
    out = asyncio.run(_collect(_DeepgramStream(ws)))
    assert out == [("guten", False), ("guten tag", True)]


def test_deepgram_send_audio_and_finish():
    from resona_cloud_stt.providers.deepgram import _DeepgramStream
    ws = _FakeProviderWS()
    s = _DeepgramStream(ws)
    asyncio.run(s.send_audio(b"\x00\x01"))
    assert ws.sent == [b"\x00\x01"]              # raw binary frame
    asyncio.run(s.finish())
    assert json.loads(ws.sent[-1]) == {"type": "CloseStream"}


# ── ElevenLabs ───────────────────────────────────────────────────────

def test_elevenlabs_open_stream_url_and_headers(monkeypatch):
    import resona_cloud_stt.providers.elevenlabs as el
    ws = _FakeProviderWS()
    captured = {}

    async def fake_connect(url, **kw):
        captured["url"] = url
        captured["kw"] = kw
        return ws

    monkeypatch.setattr("websockets.connect", fake_connect)
    asyncio.run(el.open_stream(api_key="k", model="scribe_v1", language="de", sample_rate=16000))
    assert captured["url"].startswith("wss://api.elevenlabs.io/v1/speech-to-text/realtime?")
    assert "audio_format=pcm_16000" in captured["url"]
    assert "commit_strategy=vad" in captured["url"]
    assert "language_code=de" in captured["url"]
    assert captured["kw"]["additional_headers"]["xi-api-key"] == "k"


def test_elevenlabs_audio_format_mapping():
    from resona_cloud_stt.providers.elevenlabs import _audio_format
    assert _audio_format(16000) == "pcm_16000"
    assert _audio_format(44100) == "pcm_44100"
    assert _audio_format(12345) == "pcm_16000"  # unsupported → default


def test_elevenlabs_transcript_parsing():
    from resona_cloud_stt.providers.elevenlabs import _ElevenLabsStream
    ws = _FakeProviderWS([
        json.dumps({"message_type": "session_started", "session_id": "x"}),
        json.dumps({"message_type": "partial_transcript", "text": "guten"}),
        json.dumps({"message_type": "committed_transcript", "text": "guten tag"}),
    ])
    out = asyncio.run(_collect(_ElevenLabsStream(ws, sample_rate=16000)))
    assert out == [("guten", False), ("guten tag", True)]


def test_elevenlabs_send_audio_and_commit():
    from resona_cloud_stt.providers.elevenlabs import _ElevenLabsStream
    ws = _FakeProviderWS()
    s = _ElevenLabsStream(ws, sample_rate=16000)
    asyncio.run(s.send_audio(b"\x00\x01\x02\x03"))
    chunk = json.loads(ws.sent[0])
    assert chunk["message_type"] == "input_audio_chunk"
    assert chunk["commit"] is False
    assert base64.b64decode(chunk["audio_base_64"]) == b"\x00\x01\x02\x03"
    asyncio.run(s.finish())
    assert json.loads(ws.sent[-1])["commit"] is True
