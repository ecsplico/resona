"""Tests for the Parakeet (NeMo) engine — NeMo is mocked (Linux/CUDA-only dep)."""
import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pytest

from resona_asr_core.protocol import StreamingTranscriber, Transcriber


# ── Pure helpers (no NeMo needed) ────────────────────────────────────

def test_text_delta_appends():
    from resona_engine_parakeet.transcriber import _text_delta
    assert _text_delta("hallo", "hallo welt") == "welt"
    assert _text_delta("", "hallo") == "hallo"
    assert _text_delta("hallo welt", "hallo welt") == ""


def test_text_delta_revision_returns_full():
    from resona_engine_parakeet.transcriber import _text_delta
    # A non-prefix revision returns the whole current text (lose nothing).
    assert _text_delta("hallo welt", "hallo neue welt") == "hallo neue welt"


def test_hypothesis_text_shapes():
    from resona_engine_parakeet.transcriber import _hypothesis_text
    assert _hypothesis_text(["hallo welt"]) == "hallo welt"
    hyp = types.SimpleNamespace(text="hallo welt")
    assert _hypothesis_text([hyp]) == "hallo welt"
    assert _hypothesis_text(([hyp], ["other"])) == "hallo welt"  # (best, all_hyps) tuple
    assert _hypothesis_text([]) == ""
    assert _hypothesis_text(None) == ""


# ── NeMo mocking ─────────────────────────────────────────────────────

def _install_fake_nemo(monkeypatch, model):
    """Wire a fake nemo.collections.asr.models.ASRModel returning `model`."""
    for name in (
        "nemo", "nemo.collections", "nemo.collections.asr",
        "nemo.collections.asr.models",
        "nemo.collections.asr.parts",
        "nemo.collections.asr.parts.utils",
        "nemo.collections.asr.parts.utils.streaming_utils",
    ):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    asr_model = MagicMock()
    asr_model.from_pretrained.return_value = model
    sys.modules["nemo.collections.asr.models"].ASRModel = asr_model
    sys.modules["nemo.collections.asr.parts.utils.streaming_utils"].\
        CacheAwareStreamingAudioBuffer = MagicMock()
    return asr_model


def _batch_model(text="hallo welt", streaming=False):
    model = MagicMock()
    model.transcribe.return_value = [types.SimpleNamespace(text=text)]
    if not streaming:
        # No cache-aware streaming surface → batch-only engine.
        del model.conformer_stream_step
    return model


def test_satisfies_transcriber_protocol(monkeypatch):
    _install_fake_nemo(monkeypatch, _batch_model())
    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(device="cpu", modelname="nvidia/parakeet-tdt-0.6b-v3")
    assert isinstance(t, Transcriber)


def test_transcribe_returns_text(monkeypatch):
    _install_fake_nemo(monkeypatch, _batch_model(text="guten morgen"))
    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(modelname="x")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")
    assert result["text"] == "guten morgen"
    assert result["language"] == "de"
    assert result["segments"] == []


def test_transcribe_ignores_live_kwargs(monkeypatch):
    """The live fallback calls with faster-whisper kwargs; they must not error."""
    _install_fake_nemo(monkeypatch, _batch_model())
    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(modelname="x")
    result = t.transcribe(
        np.zeros(16000, dtype=np.float32),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 1000},
        condition_on_previous_text=False,
        initial_prompt=" ",
        word_timestamps=True,
    )
    assert result["text"] == "hallo welt"


def test_not_streaming_by_default(monkeypatch):
    """Without the opt-in flag the engine is a plain batch Transcriber."""
    monkeypatch.setattr(
        "resona_engine_parakeet.transcriber.STREAMING_ENABLED", False, raising=True
    )
    _install_fake_nemo(monkeypatch, _batch_model(streaming=True))
    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(modelname="x")
    assert not isinstance(t, StreamingTranscriber)


def test_streaming_disabled_when_model_unsupported(monkeypatch):
    """Opt-in flag set but a non-cache-aware model → still batch only."""
    monkeypatch.setattr(
        "resona_engine_parakeet.transcriber.STREAMING_ENABLED", True, raising=True
    )
    _install_fake_nemo(monkeypatch, _batch_model(streaming=False))
    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(modelname="x")
    assert not isinstance(t, StreamingTranscriber)


# ── Native streaming session ─────────────────────────────────────────

class _FakeStreamBuffer:
    """Yields one chunk per appended audio block; only un-consumed chunks iterate."""
    def __init__(self, model=None):
        self._pending = []

    def append_audio(self, audio, stream_id=0):
        self._pending.append((audio, len(audio)))

    def __iter__(self):
        pending, self._pending = self._pending, []
        return iter(pending)


def test_stream_session_emits_deltas(monkeypatch):
    monkeypatch.setattr(
        "resona_engine_parakeet.transcriber.STREAMING_ENABLED", True, raising=True
    )
    model = MagicMock()
    model.transcribe.return_value = [types.SimpleNamespace(text="x")]
    model.encoder.get_initial_cache_state.return_value = (0, 0, 0)
    # Scripted cumulative transcripts across successive chunks.
    scripts = ["hallo", "hallo welt"]
    calls = {"i": 0}

    def _step(**kwargs):
        text = scripts[min(calls["i"], len(scripts) - 1)]
        calls["i"] += 1
        return (None, [types.SimpleNamespace(text=text)], 0, 0, 0, None)

    model.conformer_stream_step.side_effect = _step

    _install_fake_nemo(monkeypatch, model)
    sys.modules["nemo.collections.asr.parts.utils.streaming_utils"].\
        CacheAwareStreamingAudioBuffer = _FakeStreamBuffer

    from resona_engine_parakeet.transcriber import ParakeetTranscriber
    t = ParakeetTranscriber(modelname="x")
    assert isinstance(t, StreamingTranscriber)

    session = t.stream_session(language="de")
    u1 = session.feed(np.zeros(8000, dtype=np.float32))
    assert u1["confirmed_delta"] == "hallo"
    u2 = session.feed(np.zeros(8000, dtype=np.float32))
    assert u2["confirmed_delta"] == "welt"
    # No new audio/text → flush yields empty delta, not a crash.
    final = session.flush()
    assert final["confirmed_delta"] == ""
    assert final["language"] == "de"
