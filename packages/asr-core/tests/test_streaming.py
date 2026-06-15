"""Tests for the StreamingTranscriber protocol and LiveTranscriber native facade."""
import numpy as np

import resona_asr_core.live_transcriber as lt_mod
from resona_asr_core.live_transcriber import LiveTranscriber, SAMPLE_RATE
from resona_asr_core.protocol import StreamingTranscriber, StreamSession


class _FakeSession:
    def __init__(self):
        self.fed = []
        self.flushed = False

    def feed(self, audio):
        self.fed.append(len(audio))
        return {"confirmed_delta": "hello", "partial": "world", "language": "en"}

    def flush(self):
        self.flushed = True
        return {"confirmed_delta": "done", "partial": "", "language": "en"}


class _FakeStreamingEngine:
    def __init__(self):
        self.sessions = []

    def stream_session(self, *, language="de", task="transcribe"):
        s = _FakeSession()
        self.sessions.append(s)
        return s


class _PlainEngine:
    def transcribe(self, audio, **kwargs):
        return {"text": "x", "language": "de", "segments": []}


def test_streaming_protocol_membership():
    assert isinstance(_FakeStreamingEngine(), StreamingTranscriber)
    assert isinstance(_FakeSession(), StreamSession)
    # A non-streaming engine must NOT satisfy the streaming protocol.
    assert not isinstance(_PlainEngine(), StreamingTranscriber)


def test_facade_uses_native_session(monkeypatch):
    engine = _FakeStreamingEngine()
    monkeypatch.setattr(lt_mod, "get_transcriber", lambda *a, **k: engine)

    lt = LiveTranscriber(language="en")
    lt.add_audio(np.zeros(SAMPLE_RATE * 3, dtype=np.float32))  # 3s

    result = lt.process_sync()
    assert lt._native is True
    assert engine.sessions[0].fed  # audio was fed to the native session
    assert result is not None
    assert result.confirmed == "hello"
    assert result.partial == "world"
    assert result.confirmed_delta == "hello"
    assert result.language == "en"

    flushed = lt.flush_sync()
    assert engine.sessions[0].flushed is True
    assert flushed.confirmed == "hello done"


def test_facade_accumulates_confirmed(monkeypatch):
    engine = _FakeStreamingEngine()
    monkeypatch.setattr(lt_mod, "get_transcriber", lambda *a, **k: engine)

    lt = LiveTranscriber(language="en")
    lt.add_audio(np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    lt.process_sync()
    lt.add_audio(np.zeros(SAMPLE_RATE * 1, dtype=np.float32))  # 1s, native threshold 0.5
    assert lt.has_enough_audio() is True
    result = lt.process_sync()
    assert result.confirmed == "hello hello"  # delta appended each feed
