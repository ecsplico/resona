"""Tests for the remote live-transcription backend (RemoteLiveTranscriber)."""
import json
import threading
import time

import numpy as np
import pytest

from resona_cli.remote_live import (
    GatewayLiveTranscriber,
    RemoteLiveTranscriber,
    _build_ws_url,
    _pcm_b64,
    _to_ws_url,
)


# ── URL normalization ────────────────────────────────────────────────

@pytest.mark.parametrize("url, expected_prefix", [
    ("http://host:7001", "ws://host:7001/ws/live"),
    ("https://host", "wss://host/ws/live"),
    ("ws://host:7001", "ws://host:7001/ws/live"),
    ("wss://host/ws/live", "wss://host/ws/live"),
    ("host:7001", "ws://host:7001/ws/live"),
])
def test_to_ws_url_normalizes(url, expected_prefix):
    out = _to_ws_url(url, "de")
    assert out.startswith(expected_prefix)
    assert "language=de" in out


def test_to_ws_url_keeps_existing_query_and_language():
    out = _to_ws_url("ws://host/ws/live?engine=parakeet", "en")
    assert "engine=parakeet" in out
    assert "language=en" in out


def test_to_ws_url_does_not_clobber_explicit_language():
    out = _to_ws_url("ws://host/ws/live?language=fr", "de")
    assert "language=fr" in out
    assert "language=de" not in out


# ── PCM encoding ─────────────────────────────────────────────────────

def test_pcm_b64_roundtrips_int16():
    import base64
    audio = np.array([0.0, 1.0, -1.0, 0.5], dtype=np.float32)
    decoded = np.frombuffer(base64.b64decode(_pcm_b64(audio)), dtype="<i2")
    assert decoded[0] == 0
    assert decoded[1] == 32767
    assert decoded[2] == -32767
    assert decoded[3] == pytest.approx(16383, abs=1)


# ── Result mapping ───────────────────────────────────────────────────

def test_to_result_partial_then_final_accumulates():
    t = RemoteLiveTranscriber("ws://x", language="de")
    r1 = t._to_result("partial", {"text": "wel", "confirmed": "hallo", "delta": "hallo"})
    assert r1.confirmed == "hallo"
    assert r1.partial == "wel"
    assert r1.confirmed_delta == "hallo"
    r2 = t._to_result("final", {"text": "hallo welt", "delta": "welt"})
    assert r2.confirmed == "hallo welt"
    assert r2.partial == ""
    assert r2.confirmed_delta == "welt"
    assert t.get_full_transcript() == "hallo welt"


def test_to_result_error_returns_none():
    t = RemoteLiveTranscriber("ws://x")
    assert t._to_result("error", {"message": "boom"}) is None


# ── Threaded integration with a fake websocket ───────────────────────

class _FakeWS:
    """Minimal websockets-sync stand-in: scripts recv, records sends."""
    def __init__(self, script):
        self._script = list(script)
        self.sent = []
        self._lock = threading.Lock()
        self._emitted_terminal = False
        self.closed = False

    def send(self, msg):
        with self._lock:
            self.sent.append(msg)

    def recv(self, timeout=None):
        with self._lock:
            if self._script:
                return self._script.pop(0)
            stop_seen = any('"stop"' in m for m in self.sent)
            close_seen = any("CloseStream" in m for m in self.sent)
        # Emit the protocol-appropriate terminal once the finish frame is seen,
        # so flush waits for the finish frame to be sent (no send/close race).
        if not self._emitted_terminal:
            if close_seen:
                self._emitted_terminal = True
                return json.dumps({"type": "Metadata"})
            if stop_seen:
                self._emitted_terminal = True
                return json.dumps({"type": "stopped"})
        time.sleep(0.005)
        raise TimeoutError()

    def close(self):
        self.closed = True


def _wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_stream_pulls_results_and_flushes():
    ws = _FakeWS([
        json.dumps({"type": "partial", "text": "wel", "confirmed": "hallo", "delta": "hallo"}),
        json.dumps({"type": "final", "text": "hallo welt", "delta": "welt"}),
    ])
    t = RemoteLiveTranscriber("http://host:7001", language="de", connect=lambda url, **kw: ws)
    t.start()

    # Audio frames are encoded and queued for the sender.
    t.add_audio(np.zeros(1600, dtype=np.float32))

    assert _wait_for(lambda: t._results.qsize() >= 2), "results never arrived"

    r1 = t.process_sync()
    assert r1.confirmed_delta == "hallo"
    assert r1.partial == "wel"
    r2 = t.process_sync()
    assert r2.confirmed_delta == "welt"
    assert t.process_sync() is None

    final = t.flush_sync()
    assert final.confirmed == "hallo welt"
    # The sender forwarded audio and the stop control frame.
    assert any('"type": "audio"' in m for m in ws.sent)
    assert any('"stop"' in m for m in ws.sent)
    assert ws.closed


def test_connect_failure_is_surfaced_not_raised():
    def _boom(url, **kw):
        raise OSError("connection refused")

    t = RemoteLiveTranscriber("ws://host", connect=_boom)
    t.start()
    assert _wait_for(lambda: t._connect_error is not None)
    # An error is queued; process_sync maps it to None rather than raising.
    assert t.has_enough_audio()
    assert t.process_sync() is None
    # flush returns cleanly without waiting on a stopped ack.
    final = t.flush_sync()
    assert final.confirmed == ""


# ── Gateway backend (Deepgram /v1/listen protocol) ───────────────────

def test_gateway_url_targets_v1_listen():
    url = _build_ws_url("http://host:7000", "/v1/listen",
                        {"engine": "deepgram", "language": "de", "encoding": "linear16"})
    assert url.startswith("ws://host:7000/v1/listen?")
    assert "engine=deepgram" in url
    assert "encoding=linear16" in url


def test_gateway_classify_results_and_metadata():
    t = GatewayLiveTranscriber("ws://h", engine="deepgram", language="de")
    interim = t._classify({"type": "Results", "is_final": False,
                           "channel": {"alternatives": [{"transcript": "guten"}]}})
    assert interim == ("result", (None, "", "guten"))
    final = t._classify({"type": "Results", "is_final": True,
                         "channel": {"alternatives": [{"transcript": "guten tag"}]}})
    assert final == ("result", (None, "guten tag", ""))
    assert t._classify({"type": "Metadata"}) == ("end", None)
    assert t._classify({"type": "SpeechStarted"}) is None


def test_gateway_finish_frame_is_closestream():
    t = GatewayLiveTranscriber("ws://h", engine="elevenlabs")
    assert json.loads(t._finish_frame()) == {"type": "CloseStream"}


def test_gateway_sends_auth_header_when_key_present(monkeypatch):
    monkeypatch.setenv("RESONA_API_KEY", "secret")
    t = GatewayLiveTranscriber("ws://h", engine="deepgram")
    assert t._headers == {"Authorization": "Token secret"}


def test_gateway_stream_accumulates_finals():
    ws = _FakeWS([
        json.dumps({"type": "Results", "is_final": True,
                    "channel": {"alternatives": [{"transcript": "guten tag"}]}}),
        json.dumps({"type": "Results", "is_final": True,
                    "channel": {"alternatives": [{"transcript": "welt"}]}}),
    ])
    t = GatewayLiveTranscriber("http://host:7000", engine="deepgram", language="de",
                               connect=lambda url, **kw: ws)
    t.start()
    t.add_audio(np.zeros(1600, dtype=np.float32))

    assert _wait_for(lambda: t._results.qsize() >= 2), "results never arrived"
    r1 = t.process_sync()
    assert r1.confirmed_delta == "guten tag"
    r2 = t.process_sync()
    assert r2.confirmed_delta == "welt"
    assert t.get_full_transcript() == "guten tag welt"

    t.flush_sync()
    assert any('"type": "audio"' in m for m in ws.sent)
    assert any("CloseStream" in m for m in ws.sent)
    assert ws.closed
