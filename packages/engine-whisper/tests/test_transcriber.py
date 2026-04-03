from unittest.mock import patch, MagicMock
import numpy as np
from resona_engine_core.protocol import Transcriber
from resona_engine_whisper.transcriber import WhisperTranscriber


@patch("resona_engine_whisper.transcriber.whisper")
def test_satisfies_protocol(mock_whisper):
    mock_whisper.load_model.return_value = MagicMock()
    t = WhisperTranscriber(device="cpu", modelname="tiny")
    assert isinstance(t, Transcriber)


@patch("resona_engine_whisper.transcriber.whisper")
def test_transcribe_returns_expected_keys(mock_whisper):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "text": "hello world",
        "language": "en",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
    }
    mock_whisper.load_model.return_value = mock_model

    t = WhisperTranscriber(device="cpu", modelname="tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="en")

    assert result["text"] == "hello world"
    assert result["language"] == "en"
    assert len(result["segments"]) == 1


@patch("resona_engine_whisper.transcriber.whisper")
def test_initial_prompt_passed_through(mock_whisper):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "", "language": "de", "segments": []}
    mock_whisper.load_model.return_value = mock_model

    t = WhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000), initial_prompt="test prompt")

    call_kwargs = mock_model.transcribe.call_args[1]
    assert call_kwargs.get("initial_prompt") == "test prompt"


@patch("resona_engine_whisper.transcriber.whisper")
def test_initial_prompt_omitted_when_none(mock_whisper):
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "", "language": "de", "segments": []}
    mock_whisper.load_model.return_value = mock_model

    t = WhisperTranscriber(device="cpu", modelname="tiny")
    t.transcribe(np.zeros(16000))

    call_kwargs = mock_model.transcribe.call_args[1]
    assert "initial_prompt" not in call_kwargs
