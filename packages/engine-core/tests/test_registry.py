# packages/engine-core/tests/test_registry.py
from unittest.mock import patch, MagicMock
import numpy as np
import pytest

from resona_engine_core.protocol import Transcriber, TranscriptionResult
from resona_engine_core.registry import get_transcriber, _load_from_entrypoint, reset


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


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_load_from_entrypoint_finds_backend(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t = _load_from_entrypoint()
    assert isinstance(t, Transcriber)
    assert t.device == "cpu"


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_load_from_entrypoint_raises_on_missing(mock_config, mock_eps):
    mock_config.return_value = "nonexistent"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    with pytest.raises(ValueError, match="not found"):
        _load_from_entrypoint()


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_get_transcriber_is_singleton(mock_config, mock_eps):
    mock_config.return_value = "fake"
    mock_eps.return_value = [_make_entry_point("fake", FakeTranscriber)]
    t1 = get_transcriber()
    t2 = get_transcriber()
    assert t1 is t2


@patch("resona_engine_core.registry.entry_points")
@patch("resona_engine_core.registry.config")
def test_explicit_backend_name(mock_config, mock_eps):
    mock_eps.return_value = [_make_entry_point("specific", FakeTranscriber)]
    t = _load_from_entrypoint(backend="specific")
    assert isinstance(t, Transcriber)
    mock_config.assert_not_called()
