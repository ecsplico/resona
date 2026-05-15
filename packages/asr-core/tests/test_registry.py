# packages/engine-server/tests/test_registry.py
from unittest.mock import patch, MagicMock
import sys
import types
import numpy as np
import pytest

from resona_asr_core.protocol import Transcriber, TranscriptionResult
from resona_asr_core.registry import get_transcriber, _load_from_entrypoint, reset


class FakeTranscriber:
    def __init__(self, device: str = "cpu", modelname: str | None = None):
        self.device = device

    def transcribe(
        self, audio: np.ndarray, *, language="de", task="transcribe",
        initial_prompt=None, word_timestamps=False, vad_filter=False, **kwargs
    ) -> TranscriptionResult:
        return TranscriptionResult(text="test", language="de", segments=[])


def _make_entry_point(name: str, cls):
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


def setup_function():
    reset()


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_load_from_entrypoint_finds_backend(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t = _load_from_entrypoint()
    assert isinstance(t, Transcriber)
    assert t.device == "cpu"


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_load_from_entrypoint_raises_on_missing(mock_config, mock_eps):
    mock_config.return_value = "nonexistent"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    with pytest.raises(ValueError, match="not found"):
        _load_from_entrypoint()


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_get_transcriber_is_singleton(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t1 = get_transcriber()
    t2 = get_transcriber()
    assert t1 is t2


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_explicit_backend_name(mock_config, mock_eps):
    mock_eps.return_value = [_make_entry_point("specific", FakeTranscriber)]
    t = _load_from_entrypoint(backend="specific")
    assert isinstance(t, Transcriber)
    mock_config.assert_not_called()


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_registry_selects_correct_backend_from_multiple(mock_config, mock_eps):
    """When multiple backends are registered, the correct one is selected by name."""
    class OtherTranscriber:
        def __init__(self, device: str = "cpu", modelname: str | None = None):
            self.device = device

        def transcribe(
            self, audio: np.ndarray, *, language="de", task="transcribe",
            initial_prompt=None, word_timestamps=False, vad_filter=False, **kwargs
        ) -> TranscriptionResult:
            return TranscriptionResult(text="other", language="de", segments=[])

    mock_config.return_value = "other"
    mock_eps.return_value = [
        _make_entry_point("fake", FakeTranscriber),
        _make_entry_point("other", OtherTranscriber),
    ]
    t = _load_from_entrypoint()
    assert isinstance(t, OtherTranscriber)
    assert isinstance(t, Transcriber)


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_registry_protocol_violation_raises(mock_config, mock_eps):
    """Backend that doesn't satisfy Transcriber protocol should raise AssertionError."""
    class BadBackend:
        def __init__(self, device: str = "cpu"):
            pass
        # Missing transcribe method

    mock_config.return_value = "bad"
    mock_eps.return_value = [_make_entry_point("bad", BadBackend)]

    with pytest.raises(AssertionError, match="does not satisfy"):
        _load_from_entrypoint()


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_reset_clears_singleton(mock_config, mock_eps):
    """After loading a transcriber, reset() causes get_transcriber() to load a fresh instance."""
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]

    t1 = get_transcriber()
    reset()
    t2 = get_transcriber()
    assert t1 is not t2
    assert isinstance(t2, FakeTranscriber)


def test_detect_device_uses_ctranslate2_when_torch_absent(monkeypatch):
    """With torch unavailable, _detect_device falls back to CTranslate2."""
    import resona_asr_core.registry as reg

    # Make `import torch` raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)

    fake_ct2 = types.ModuleType("ctranslate2")
    fake_ct2.get_cuda_device_count = lambda: 0
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    assert reg._detect_device() == "cpu"

    fake_ct2.get_cuda_device_count = lambda: 1
    assert reg._detect_device() == "cuda"


def test_detect_device_cpu_when_nothing_available(monkeypatch):
    """No torch and no ctranslate2 -> cpu."""
    import resona_asr_core.registry as reg

    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    assert reg._detect_device() == "cpu"
