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


@patch("resona_asr_core.registry._detect_device", return_value="cpu")
@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_load_from_entrypoint_finds_engine(mock_config, mock_eps, mock_detect):
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
def test_explicit_engine_name(mock_config, mock_eps):
    mock_eps.return_value = [_make_entry_point("specific", FakeTranscriber)]
    t = _load_from_entrypoint(engine="specific")
    assert isinstance(t, Transcriber)
    mock_config.assert_not_called()


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_registry_selects_correct_engine_from_multiple(mock_config, mock_eps):
    """When multiple engines are registered, the correct one is selected by name."""
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
    """Engine that doesn't satisfy Transcriber protocol should raise AssertionError."""
    class BadEngine:
        def __init__(self, device: str = "cpu"):
            pass
        # Missing transcribe method

    mock_config.return_value = "bad"
    mock_eps.return_value = [_make_entry_point("bad", BadEngine)]

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


def test_recommended_engine_apple_silicon_prefers_mlx(monkeypatch):
    """On Apple Silicon, the GPU-native engines win over faster-whisper."""
    import resona_asr_core.registry as reg

    monkeypatch.setattr(reg, "_is_apple_silicon", lambda: True)
    installed = ["faster-whisper", "whisper-cpp", "mlx-whisper"]
    assert reg.recommended_engine(installed) == "mlx-whisper"
    # Without mlx-whisper, the next Apple-Silicon preference is lightning then cpp.
    assert reg.recommended_engine(["faster-whisper", "whisper-cpp"]) == "whisper-cpp"


def test_recommended_engine_non_apple_prefers_faster_whisper(monkeypatch):
    """Off Apple Silicon (e.g. Linux GPU/CPU), faster-whisper is the default."""
    import resona_asr_core.registry as reg

    monkeypatch.setattr(reg, "_is_apple_silicon", lambda: False)
    installed = ["mlx-whisper", "faster-whisper", "whisper"]
    assert reg.recommended_engine(installed) == "faster-whisper"


def test_recommended_engine_falls_back_to_any_installed(monkeypatch):
    """If no preferred engine is present, return some installed engine."""
    import resona_asr_core.registry as reg

    monkeypatch.setattr(reg, "_is_apple_silicon", lambda: False)
    assert reg.recommended_engine(["voxtral"]) == "voxtral"
    # Nothing installed -> the faster-whisper name as a last resort.
    assert reg.recommended_engine([]) == "faster-whisper"


@patch("resona_asr_core.registry.entry_points")
@patch("resona_asr_core.registry.config")
def test_load_uses_recommended_when_env_unset(mock_config, mock_eps, monkeypatch):
    """With RESONA_ENGINE unset (config returns ''), the environment default is used."""
    import resona_asr_core.registry as reg

    mock_config.return_value = ""  # RESONA_ENGINE not set
    monkeypatch.setattr(reg, "_is_apple_silicon", lambda: True)
    mock_eps.return_value = [
        _make_entry_point("faster-whisper", FakeTranscriber),
        _make_entry_point("mlx-whisper", FakeTranscriber),
    ]
    # mlx-whisper is the Apple-Silicon recommendation; it must be the one loaded.
    with patch.object(reg, "recommended_engine", wraps=reg.recommended_engine) as rec:
        _load_from_entrypoint()
        rec.assert_called_once()
