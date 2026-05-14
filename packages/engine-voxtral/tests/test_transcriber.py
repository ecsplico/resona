from unittest.mock import patch, MagicMock
import numpy as np
from resona_asr_core.protocol import Transcriber
from resona_engine_voxtral.transcriber import VoxtralTranscriber


def _mock_pipeline_output(text="hello world", chunks=None):
    if chunks is None:
        chunks = [{"text": "hello world", "timestamp": (0.0, 1.0)}]
    return {"text": text, "chunks": chunks}


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_satisfies_protocol(mock_pipeline):
    mock_pipeline.return_value = MagicMock()
    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    assert isinstance(t, Transcriber)


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_transcribe_returns_expected_keys(mock_pipeline):
    mock_pipe = MagicMock()
    mock_pipe.return_value = _mock_pipeline_output()
    mock_pipeline.return_value = mock_pipe

    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="en")

    assert result["text"] == "hello world"
    assert result["language"] == "en"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 1.0


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_initial_prompt_passed_through(mock_pipeline):
    mock_pipe = MagicMock()
    mock_pipe.return_value = _mock_pipeline_output()
    mock_pipeline.return_value = mock_pipe

    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    t.transcribe(np.zeros(16000), initial_prompt="medical terminology")

    call_kwargs = mock_pipe.call_args[1]
    assert call_kwargs.get("initial_prompt") == "medical terminology"


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_initial_prompt_omitted_when_none(mock_pipeline):
    mock_pipe = MagicMock()
    mock_pipe.return_value = _mock_pipeline_output()
    mock_pipeline.return_value = mock_pipe

    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    t.transcribe(np.zeros(16000))

    call_kwargs = mock_pipe.call_args[1]
    assert "initial_prompt" not in call_kwargs


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_language_and_task_in_generate_kwargs(mock_pipeline):
    mock_pipe = MagicMock()
    mock_pipe.return_value = _mock_pipeline_output()
    mock_pipeline.return_value = mock_pipe

    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    t.transcribe(np.zeros(16000), language="fr", task="translate")

    call_kwargs = mock_pipe.call_args[1]
    gen_kwargs = call_kwargs["generate_kwargs"]
    assert gen_kwargs["language"] == "fr"
    assert gen_kwargs["task"] == "translate"


@patch("resona_engine_voxtral.transcriber.hf_pipeline")
def test_multiple_chunks_become_segments(mock_pipeline):
    mock_pipe = MagicMock()
    mock_pipe.return_value = _mock_pipeline_output(
        text="hello world goodbye",
        chunks=[
            {"text": "hello world", "timestamp": (0.0, 1.0)},
            {"text": " goodbye", "timestamp": (1.0, 2.0)},
        ],
    )
    mock_pipeline.return_value = mock_pipe

    t = VoxtralTranscriber(device="cpu", modelname="openai/whisper-tiny")
    result = t.transcribe(np.zeros(32000))

    assert len(result["segments"]) == 2
    assert result["segments"][1]["text"] == " goodbye"
    assert result["segments"][1]["start"] == 1.0
