"""Tests for the Deepgram-compatible WS /v1/listen streaming proxy."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from resona_api import streaming_routes as sr
from resona_api import engine_registry as reg


# ── Pure-helper unit tests ───────────────────────────────────────────────────

def test_truthy():
    assert sr._truthy("true") is True
    assert sr._truthy("1") is True
    assert sr._truthy("on") is True
    assert sr._truthy("false") is False
    assert sr._truthy(None, default=True) is True
    assert sr._truthy(None) is False


def test_ws_url():
    assert sr._ws_url("http://e:7001", "de") == "ws://e:7001/ws/live?language=de"
    assert sr._ws_url("https://e:7001/", "en") == "wss://e:7001/ws/live?language=en"


def test_deepgram_results_shape():
    msg = sr.deepgram_results("hallo welt", is_final=True)
    assert msg["type"] == "Results"
    assert msg["is_final"] is True
    assert msg["channel"]["alternatives"][0]["transcript"] == "hallo welt"
    assert msg["channel"]["alternatives"][0]["confidence"] == 1.0


def test_deepgram_metadata_shape():
    msg = sr.deepgram_metadata("req-1", models=["large-v3"])
    assert msg["type"] == "Metadata"
    assert msg["request_id"] == "req-1"
    assert msg["models"] == ["large-v3"]


# ── Fake engine WebSocket (stands in for engine-server /ws/live) ─────────────

_SENTINEL = object()


class _FakeEngineWS:
    """Records frames sent by the proxy; emits scripted transcripts after stop."""

    def __init__(self, script):
        self.sent = []
        self._script = script
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, msg):
        self.sent.append(json.loads(msg))
        if json.loads(msg)["type"] == "stop":
            for line in self._script:
                await self._queue.put(json.dumps(line))
            await self._queue.put(json.dumps({"type": "stopped"}))
            await self._queue.put(_SENTINEL)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is _SENTINEL:
            raise StopAsyncIteration
        return item


class _FakeConnect:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def streaming_client(monkeypatch):
    """A TestClient over an app exposing only the streaming router, with a fake engine."""
    fake_ws = _FakeEngineWS(script=[
        {"type": "partial", "text": "guten tag", "confirmed": "", "delta": ""},
        {"type": "final", "text": "guten tag welt", "delta": "guten tag welt"},
    ])

    monkeypatch.setattr(sr.websockets, "connect", lambda url, **k: _FakeConnect(fake_ws))
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: reg.EngineInfo(
        name="faster-whisper", kind="local", capabilities=["stt"],
        private=True, available=True, models=["large-v3"], url="http://engine:7001",
    ))

    app = FastAPI()
    app.include_router(sr.router)
    with TestClient(app) as c:
        yield c, fake_ws


def test_listen_bridges_audio_and_emits_results(streaming_client):
    client, fake_ws = streaming_client
    with client.websocket_connect("/v1/listen?encoding=linear16&sample_rate=16000") as ws:
        ws.send_bytes(b"\x00\x01" * 160)  # one PCM frame
        ws.send_text(json.dumps({"type": "CloseStream"}))

        messages = []
        while True:
            msg = ws.receive_json()
            messages.append(msg)
            if msg["type"] == "Metadata":
                break

    # Proxy forwarded the audio frame to the engine, then a stop.
    types_sent = [m["type"] for m in fake_ws.sent]
    assert "audio" in types_sent
    assert "stop" in types_sent

    # The confirmed delta became a final Results; Metadata closed the stream.
    finals = [m for m in messages if m["type"] == "Results" and m["is_final"]]
    assert any(
        m["channel"]["alternatives"][0]["transcript"] == "guten tag welt" for m in finals
    )
    assert messages[-1]["type"] == "Metadata"


def test_listen_interim_disabled_by_default(streaming_client):
    client, _ = streaming_client
    with client.websocket_connect("/v1/listen?encoding=linear16") as ws:
        ws.send_text(json.dumps({"type": "CloseStream"}))
        msgs = []
        while True:
            m = ws.receive_json()
            msgs.append(m)
            if m["type"] == "Metadata":
                break
    # No interim (is_final=false) results when interim_results is unset.
    assert not any(m["type"] == "Results" and not m["is_final"] for m in msgs)


def test_listen_rejects_unsupported_encoding(streaming_client):
    client, _ = streaming_client
    with client.websocket_connect("/v1/listen?encoding=opus") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "Error"
        assert "encoding" in msg["description"]


def test_listen_rejects_non_streaming_cloud(monkeypatch):
    """A cloud provider without a realtime API (OpenAI) is still rejected."""
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: reg.EngineInfo(
        name="openai", kind="cloud", capabilities=["stt", "tts"],
        private=False, available=True, models=["whisper-1"], provider="openai",
    ))
    app = FastAPI()
    app.include_router(sr.router)
    with TestClient(app) as c:
        with c.websocket_connect("/v1/listen?engine=openai&encoding=linear16") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "Error"
            assert "streaming" in msg["description"]


# ── Cloud streaming bridge (Deepgram / ElevenLabs via resona_cloud_stt) ───────

class _FakeCloudSession:
    """Stands in for a resona_cloud_stt streaming session.

    Emits scripted transcripts after ``finish()`` (mimicking a provider that
    flushes finals once the client stops), then ends iteration.
    """

    def __init__(self, script):
        self._script = list(script)
        self.sent = []
        self.finished = False
        self.closed = False
        self._q: asyncio.Queue = asyncio.Queue()

    async def send_audio(self, pcm):
        self.sent.append(pcm)

    async def finish(self):
        self.finished = True
        for transcript in self._script:
            await self._q.put(transcript)
        await self._q.put(None)  # sentinel → stop iteration

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        while True:
            item = await self._q.get()
            if item is None:
                return
            yield item


def _cloud_app(monkeypatch, provider, script):
    from resona_cloud_stt.streaming import StreamTranscript  # noqa: F401 (re-exported type)
    session = _FakeCloudSession(script)
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: reg.EngineInfo(
        name=provider, kind="cloud", capabilities=["stt", "tts"],
        private=False, available=True, models=["m"], provider=provider,
    ))
    monkeypatch.setattr(reg, "cloud_api_key", lambda p: "key")
    captured = {}

    async def fake_open(prov, **kw):
        captured["provider"] = prov
        captured["kw"] = kw
        return session

    monkeypatch.setattr(sr, "open_stream", fake_open)
    app = FastAPI()
    app.include_router(sr.router)
    return app, session, captured


def test_listen_bridges_cloud_deepgram(monkeypatch):
    from resona_cloud_stt.streaming import StreamTranscript
    app, session, captured = _cloud_app(
        monkeypatch, "deepgram", [StreamTranscript("guten tag welt", True)],
    )
    with TestClient(app) as c:
        with c.websocket_connect("/v1/listen?engine=deepgram&encoding=linear16") as ws:
            ws.send_bytes(b"\x00\x01" * 160)
            ws.send_text(json.dumps({"type": "CloseStream"}))
            msgs = []
            while True:
                m = ws.receive_json()
                msgs.append(m)
                if m["type"] == "Metadata":
                    break

    assert captured["provider"] == "deepgram"
    assert session.sent          # audio forwarded upstream
    assert session.finished and session.closed
    finals = [m for m in msgs if m["type"] == "Results" and m["is_final"]]
    assert any(
        m["channel"]["alternatives"][0]["transcript"] == "guten tag welt" for m in finals
    )
    assert msgs[-1]["type"] == "Metadata"


def test_listen_cloud_interim_results(monkeypatch):
    from resona_cloud_stt.streaming import StreamTranscript
    app, _session, _ = _cloud_app(
        monkeypatch, "elevenlabs",
        [StreamTranscript("guten", False), StreamTranscript("guten tag", True)],
    )
    with TestClient(app) as c:
        with c.websocket_connect(
            "/v1/listen?engine=elevenlabs&encoding=linear16&interim_results=true"
        ) as ws:
            ws.send_text(json.dumps({"type": "CloseStream"}))
            msgs = []
            while True:
                m = ws.receive_json()
                msgs.append(m)
                if m["type"] == "Metadata":
                    break

    interims = [m for m in msgs if m["type"] == "Results" and not m["is_final"]]
    finals = [m for m in msgs if m["type"] == "Results" and m["is_final"]]
    assert any(m["channel"]["alternatives"][0]["transcript"] == "guten" for m in interims)
    assert any(m["channel"]["alternatives"][0]["transcript"] == "guten tag" for m in finals)
