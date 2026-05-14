from unittest.mock import patch, MagicMock
import numpy as np
from resona_asr_core.protocol import Transcriber
from resona_engine_faster_whisper.transcriber import FastWhisperTranscriber


def _mock_segment(text="hello", start=0.0, end=1.0):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.words = None
    return seg


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_satisfies_protocol(mock_model_cls):
    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    assert isinstance(t, Transcriber)


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_transcribe_returns_expected_keys(mock_model_cls):
    mock_model = mock_model_cls.return_value
    info = MagicMock()
    info.language = "de"
    mock_model.transcribe.return_value = (iter([_mock_segment()]), info)

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert "text" in result
    assert "language" in result
    assert "segments" in result
    assert result["language"] == "de"
    assert "hello" in result["text"]


@patch("resona_engine_faster_whisper.transcriber.WhisperModel")
def test_initial_prompt_passed_through(mock_model_cls):
    mock_model = mock_model_cls.return_value
    info = MagicMock()
    info.language = "de"
    mock_model.transcribe.return_value = (iter([]), info)

    t = FastWhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000), initial_prompt="test prompt")

    _, call_kwargs = mock_model.transcribe.call_args
    assert call_kwargs.get("initial_prompt") == "test prompt"
