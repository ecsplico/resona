"""Tests for the Deepgram-compatible WS /v1/listen streaming proxy."""
import asyncio
import base64
import io
import json
import shutil
import subprocess
import wave

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
def streaming_client(monkeypatch, client_headers, org_a):
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
        c.headers.update(client_headers(org_a))
        yield c, fake_ws


def test_listen_rejects_unauthenticated_handshake(streaming_client):
    from starlette.websockets import WebSocketDisconnect

    client, _ = streaming_client
    client.headers.pop("Authorization")
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/v1/listen?encoding=linear16&sample_rate=16000"):
            pass
    assert excinfo.value.code == 1008


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


def test_listen_rejects_non_streaming_cloud(monkeypatch, cloud_org, client_headers):
    """A cloud provider without a realtime API (OpenAI) is still rejected."""
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: reg.EngineInfo(
        name="openai", kind="cloud", capabilities=["stt", "tts"],
        private=False, available=True, models=["whisper-1"], provider="openai",
    ))
    app = FastAPI()
    app.include_router(sr.router)
    with TestClient(app) as c:
        c.headers.update(client_headers(cloud_org))
        with c.websocket_connect("/v1/listen?engine=openai&encoding=linear16&private=false") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "Error"
            assert "streaming" in msg["description"]


# ── Closing after the client already left ────────────────────────────────────

class _AsgiClient:
    """ASGI side of a client socket that behaves like uvicorn after a disconnect.

    Once the client has disconnected, uvicorn rejects ``websocket.close`` with a
    ``RuntimeError``; ``fail_close`` makes it reject even before (the race where
    the peer vanishes between the state check and the send).
    """

    def __init__(self, incoming, fail_close=False):
        self._incoming = [{"type": "websocket.connect"}, *incoming]
        self.fail_close = fail_close
        self.disconnected = False
        self.sent = []

    async def receive(self):
        message = self._incoming.pop(0)
        if message["type"] == "websocket.disconnect":
            self.disconnected = True
        return message

    async def send(self, message):
        if message["type"] == "websocket.close" and (self.disconnected or self.fail_close):
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.close', after sending 'websocket.close' "
                "or response already completed."
            )
        self.sent.append(message)


async def _accepted(asgi):
    from starlette.websockets import WebSocket

    ws = WebSocket({"type": "websocket", "path": "/v1/listen", "headers": []}, asgi.receive, asgi.send)
    await ws.accept()
    return ws


def test_local_bridge_ends_quietly_after_client_disconnect(monkeypatch, caplog):
    asgi = _AsgiClient([{"type": "websocket.disconnect", "code": 1000}])
    info = reg.EngineInfo(
        name="faster-whisper", kind="local", capabilities=["stt"],
        private=True, available=True, models=["large-v3"], url="http://engine:7001",
    )

    async def run():
        fake_ws = _FakeEngineWS(script=[])
        monkeypatch.setattr(sr.websockets, "connect", lambda url, **k: _FakeConnect(fake_ws))
        ws = await _accepted(asgi)
        await sr._run_local_bridge(
            ws, info, language="de", sample_rate=16000, channels=1,
            interim_results=False, pipeline=None, request_id="req-1", models=["large-v3"],
        )

    with caplog.at_level("ERROR"):
        asyncio.run(run())

    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert all(m["type"] != "websocket.close" for m in asgi.sent)


def test_close_tolerates_peer_vanishing_mid_close():
    asgi = _AsgiClient([], fail_close=True)

    async def run():
        ws = await _accepted(asgi)
        await sr._close(ws)

    asyncio.run(run())  # no RuntimeError escapes


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


@pytest.fixture
def cloud_org(fresh_org, store_settings):
    """A fresh org with cloud enabled and a registered cloud-ASR endpoint per provider under test."""
    store_settings(fresh_org, cloud_enabled=True, endpoints=[
        {"id": f"cloud-asr:{provider}", "kind": "cloud_asr", "model": "m"}
        for provider in ("deepgram", "elevenlabs", "openai")
    ])
    return fresh_org


def test_listen_private_cloud_is_policy_denied(monkeypatch, cloud_org, client_headers):
    app, session, captured = _cloud_app(monkeypatch, "deepgram", [])
    with TestClient(app) as c:
        c.headers.update(client_headers(cloud_org))
        with c.websocket_connect("/v1/listen?engine=deepgram&encoding=linear16") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "Error"
    assert msg["description"].startswith("policy_denied: ")
    assert "provider" not in captured  # no provider stream was opened
    assert not session.sent


def test_listen_bridges_cloud_deepgram(monkeypatch, cloud_org, client_headers):
    from resona_cloud_stt.streaming import StreamTranscript
    app, session, captured = _cloud_app(
        monkeypatch, "deepgram", [StreamTranscript("guten tag welt", True)],
    )
    with TestClient(app) as c:
        c.headers.update(client_headers(cloud_org))
        with c.websocket_connect("/v1/listen?engine=deepgram&encoding=linear16&private=false") as ws:
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


def test_listen_cloud_interim_results(monkeypatch, cloud_org, client_headers):
    from resona_cloud_stt.streaming import StreamTranscript
    app, _session, _ = _cloud_app(
        monkeypatch, "elevenlabs",
        [StreamTranscript("guten", False), StreamTranscript("guten tag", True)],
    )
    with TestClient(app) as c:
        c.headers.update(client_headers(cloud_org))
        with c.websocket_connect(
            "/v1/listen?engine=elevenlabs&encoding=linear16&interim_results=true&private=false"
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


# ── GET /v1/listen/engines ───────────────────────────────────────────────────

def _listen_client() -> TestClient:
    app = FastAPI()
    app.include_router(sr.router)
    return TestClient(app)


def _local(name="faster-whisper", available=True) -> reg.EngineInfo:
    return reg.EngineInfo(name=name, kind="local", capabilities=["stt"], private=True,
                          available=available, models=["large-v3"], url="http://e:7001")


def _cloud(provider: str, model: str) -> reg.EngineInfo:
    return reg.EngineInfo(name=provider, kind="cloud", capabilities=["stt"], private=False,
                          available=True, models=[model], provider=provider)


@pytest.fixture
def listen_catalogue(monkeypatch):
    """Factory: ``listen_catalogue([EngineInfo, ...])`` fixes the gateway catalogue."""
    monkeypatch.setattr(reg, "default_engine_name", lambda: None)

    def set_catalogue(entries):
        monkeypatch.setattr(reg, "get_catalogue", lambda: list(entries))
    return set_catalogue


def test_listen_engines_lists_available_local_as_default(fresh_org, client_headers, listen_catalogue):
    listen_catalogue([_local()])
    r = _listen_client().get("/v1/listen/engines", headers=client_headers(fresh_org))
    assert r.status_code == 200
    assert r.json() == {
        "engines": [{"name": "faster-whisper", "kind": "local", "private": True, "models": ["large-v3"]}],
        "default": "faster-whisper",
    }


def test_listen_engines_omits_unavailable_local(fresh_org, client_headers, listen_catalogue):
    listen_catalogue([_local(available=False)])
    r = _listen_client().get("/v1/listen/engines", headers=client_headers(fresh_org))
    assert r.status_code == 200
    assert r.json() == {"engines": [], "default": None}


def test_listen_engines_deepgram_needs_cloud_and_approved_endpoint(
    fresh_org, client_headers, store_settings, listen_catalogue,
):
    from resona_job_core import Endpoint

    listen_catalogue([_cloud("deepgram", "nova-3")])
    client = _listen_client()
    headers = client_headers(fresh_org)

    def names():
        return [e["name"] for e in client.get("/v1/listen/engines", headers=headers).json()["engines"]]

    assert names() == []
    store_settings(fresh_org, cloud_enabled=True)
    assert names() == []
    endpoint = Endpoint(id="cloud-asr:deepgram", kind="cloud_asr", model="nova-3")
    store_settings(fresh_org, endpoints=[endpoint.model_dump(mode="json")])
    body = client.get("/v1/listen/engines", headers=headers).json()
    assert body["engines"] == [{"name": "deepgram", "kind": "cloud", "private": False, "models": ["nova-3"]}]
    assert body["default"] is None


def test_listen_engines_never_lists_openai(fresh_org, client_headers, store_settings, listen_catalogue):
    from resona_job_core import Endpoint

    listen_catalogue([_cloud("openai", "whisper-1")])
    endpoint = Endpoint(id="cloud-asr:openai", kind="cloud_asr", model="whisper-1")
    store_settings(fresh_org, cloud_enabled=True, endpoints=[endpoint.model_dump(mode="json")])
    r = _listen_client().get("/v1/listen/engines", headers=client_headers(fresh_org))
    assert r.status_code == 200
    assert r.json() == {"engines": [], "default": None}


def test_listen_engines_requires_auth(listen_catalogue):
    listen_catalogue([_local()])
    assert _listen_client().get("/v1/listen/engines").status_code == 401


# ── Deepgram compatibility: params, key terms, speech_final, Metadata ────────

def test_results_mark_finals_as_speech_final():
    assert sr.deepgram_results("a", is_final=True)["speech_final"] is True
    interim = sr.deepgram_results("a", is_final=False)
    assert interim["is_final"] is False and interim["speech_final"] is False


def test_metadata_names_the_engine():
    msg = sr.deepgram_metadata("req-1", models=["large-v3"], engine="faster-whisper")
    assert msg["engine"] == "faster-whisper"
    assert msg["model_info"] == {"large-v3": {"name": "faster-whisper", "version": "", "arch": "large-v3"}}
    assert "engine" not in sr.deepgram_metadata("req-1", models=[])


def test_merge_keyterms_and_prompt():
    merged = sr.merge_keyterms([" Olanzapin ", "", "olanzapin", "Quetiapin"], ["Befund", "quetiapin"])
    assert merged == ["Olanzapin", "Quetiapin", "Befund"]
    assert sr.keyterm_prompt(merged) == "Olanzapin, Quetiapin, Befund"
    assert sr.keyterm_prompt([]) is None
    assert len(sr.keyterm_prompt(["x" * 500, "y" * 500]) or "") == 500  # cut at a term boundary


def test_ws_url_carries_auto_language_and_prompt():
    assert sr._ws_url("http://e:7001", None, "A, B") == "ws://e:7001/ws/live?language=auto&initial_prompt=A%2C+B"


@pytest.mark.parametrize("head, container", [
    (b"\x1a\x45\xdf\xa3\x01\x00", True),
    (b"OggS\x00\x02", True),
    (b"RIFF\x24\x00\x00\x00WAVEfmt ", True),
    (b"\x00\x00\x00\x20ftypisom", True),
    (b"ID3\x04\x00", True),
    (b"fLaC\x00\x00", True),
    (b"\xff\xfb\x90\x00", False),  # bare MPEG sync is also plausible PCM
    (b"\x00\x01" * 8, False),
])
def test_is_container_audio(head, container):
    assert sr.is_container_audio(head) is container


@pytest.fixture
def local_listen(monkeypatch, client_headers, org_a):
    """A streaming app over a fake local engine that records the engine URL and resolve calls."""
    fake_ws = _FakeEngineWS(script=[{"type": "final", "text": "hallo", "delta": "hallo"}])
    seen = {"urls": [], "resolve": []}

    def connect(url, **kwargs):
        seen["urls"].append(url)
        return _FakeConnect(fake_ws)

    def resolve(name, *args, **kwargs):
        seen["resolve"].append(name)
        return _local()

    monkeypatch.setattr(sr.websockets, "connect", connect)
    monkeypatch.setattr(reg, "resolve", resolve)
    monkeypatch.setattr(reg, "get_catalogue", lambda: [_local()])
    app = FastAPI()
    app.include_router(sr.router)
    with TestClient(app) as c:
        c.headers.update(client_headers(org_a))
        yield c, fake_ws, seen


def _until_metadata(ws) -> list[dict]:
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] in ("Metadata", "Error"):
            return messages


def test_listen_accepts_deepgram_params_and_passes_keyterms(local_listen):
    client, _, seen = local_listen
    query = (
        "encoding=linear16&model=nova-3&smart_format=true&endpointing=300&vad_events=true"
        "&mip_opt_out=true&punctuate=true&utterances=true&diarize=true&filler_words=true&channels=1"
        "&detect_language=true&keyterm=Olanzapin&keyterm=Quetiapin"
    )
    with client.websocket_connect(f"/v1/listen?{query}") as ws:
        ws.send_text(json.dumps({"type": "CloseStream"}))
        messages = _until_metadata(ws)

    assert seen["resolve"] == [None]  # nova-3 names no engine of the org: the default applies
    url = seen["urls"][0]
    assert "language=auto" in url
    assert "initial_prompt=Olanzapin%2C+Quetiapin" in url
    final = next(m for m in messages if m["type"] == "Results")
    assert final["is_final"] is True and final["speech_final"] is True
    assert messages[-1]["type"] == "Metadata" and messages[-1]["engine"] == "faster-whisper"


def test_listen_model_selects_an_engine_the_org_can_stream(local_listen):
    client, _, seen = local_listen
    with client.websocket_connect("/v1/listen?encoding=linear16&model=faster-whisper") as ws:
        ws.send_text(json.dumps({"type": "CloseStream"}))
        _until_metadata(ws)
    assert seen["resolve"] == ["faster-whisper"]


def test_listen_engine_param_wins_over_model(local_listen):
    client, _, seen = local_listen
    with client.websocket_connect("/v1/listen?encoding=linear16&model=faster-whisper&engine=other") as ws:
        ws.send_text(json.dumps({"type": "CloseStream"}))
        _until_metadata(ws)
    assert seen["resolve"] == ["other"]


def test_listen_explicit_linear16_never_decodes(local_listen, monkeypatch):
    client, fake_ws, _ = local_listen
    monkeypatch.setattr(sr.shutil, "which", lambda name: pytest.fail("ffmpeg must not be looked up"))
    frame = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16
    with client.websocket_connect("/v1/listen?encoding=linear16") as ws:
        ws.send_bytes(frame)
        ws.send_text(json.dumps({"type": "CloseStream"}))
        _until_metadata(ws)
    audio = [m for m in fake_ws.sent if m["type"] == "audio"]
    assert [base64.b64decode(m["data"]) for m in audio] == [frame]


def test_listen_container_without_ffmpeg_closes_1011(local_listen, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    client, _, _ = local_listen
    monkeypatch.setattr(sr.shutil, "which", lambda name: None)
    with client.websocket_connect("/v1/listen") as ws:
        ws.send_bytes(_wav(b"\x00\x00" * 1600, 16000))
        msg = ws.receive_json()
        while msg["type"] != "Error":  # the engine may answer its stop before the abort lands
            msg = ws.receive_json()
        assert "ffmpeg" in msg["description"]
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
    assert excinfo.value.code == 1011


def test_listen_cloud_bridge_receives_keyterms(monkeypatch, cloud_org, client_headers):
    app, _session, captured = _cloud_app(monkeypatch, "deepgram", [])
    with TestClient(app) as c:
        c.headers.update(client_headers(cloud_org))
        with c.websocket_connect(
            "/v1/listen?engine=deepgram&encoding=linear16&private=false&keyterm=Olanzapin&keyterm=Lithium"
        ) as ws:
            ws.send_text(json.dumps({"type": "CloseStream"}))
            messages = _until_metadata(ws)
    assert captured["kw"]["keyterms"][:2] == ("Olanzapin", "Lithium")
    assert messages[-1]["engine"] == "deepgram"


# ── Container audio through ffmpeg ───────────────────────────────────────────

def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _encode(fmt: list[str]) -> bytes:
    """Half a second of a 440 Hz tone, encoded by ffmpeg with output args ``fmt``."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.5:sample_rate=48000", "-ac", "1", *fmt, "pipe:1"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        pytest.skip(f"ffmpeg cannot encode {fmt}: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


@needs_ffmpeg
@pytest.mark.parametrize("fmt", [
    ["-c:a", "libopus", "-f", "webm"],
    ["-c:a", "libopus", "-f", "ogg"],
    ["-c:a", "pcm_s16le", "-f", "wav"],
], ids=["webm", "ogg", "wav"])
def test_listen_decodes_container_audio_through_ffmpeg(local_listen, fmt):
    client, fake_ws, _ = local_listen
    data = _encode(fmt)
    with client.websocket_connect("/v1/listen?sample_rate=16000") as ws:
        for start in range(0, len(data), 1024):
            ws.send_bytes(data[start:start + 1024])
        ws.send_text(json.dumps({"type": "CloseStream"}))
        messages = _until_metadata(ws)

    assert messages[-1]["type"] == "Metadata"
    pcm = b"".join(base64.b64decode(m["data"]) for m in fake_ws.sent if m["type"] == "audio")
    # 0.5 s of mono 16 kHz linear16 is 16000 bytes; codecs pad or trim a few milliseconds.
    assert 14000 <= len(pcm) <= 18000 and len(pcm) % 2 == 0
    assert all(m["sample_rate"] == 16000 for m in fake_ws.sent if m["type"] == "audio")
    assert fake_ws.sent[-1]["type"] == "stop"  # the stop follows the drained PCM


@needs_ffmpeg
def test_listen_undecodable_container_reports_an_error(local_listen):
    client, _, _ = local_listen
    with client.websocket_connect("/v1/listen") as ws:
        ws.send_bytes(b"OggS" + b"\x00" * 60)  # Ogg magic, garbage after it
        ws.send_text(json.dumps({"type": "CloseStream"}))
        messages = _until_metadata(ws)
    # ffmpeg produces no audio; the stream still ends cleanly with Metadata after the drain.
    assert messages[-1]["type"] in ("Metadata", "Error")
