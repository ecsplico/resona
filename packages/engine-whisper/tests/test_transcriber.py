import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest
from resona_asr_core.protocol import Transcriber
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


# -- vad_filter: Silero VAD (faster-whisper's bundled ONNX model, runs for real) ----

SPEECH_WAV = Path(__file__).resolve().parents[3] / "eval" / "corpus" / "de_speech.wav"


def _speech() -> np.ndarray:
    with wave.open(str(SPEECH_WAV), "rb") as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _transcriber(mock_whisper, transcribe_return) -> tuple[WhisperTranscriber, MagicMock]:
    model = MagicMock()
    model.transcribe.return_value = transcribe_return
    mock_whisper.load_model.return_value = model
    return WhisperTranscriber(device="cpu", modelname="large-v3"), model


@patch("resona_engine_whisper.transcriber.whisper")
def test_vad_noise_yields_empty_transcript_without_decoding(mock_whisper):
    # openai-whisper turns noise into "Vielen Dank."; with vad_filter it is never decoded.
    t, model = _transcriber(mock_whisper, {"text": " Vielen Dank.", "segments": []})
    noise = (np.random.default_rng(7).standard_normal(16000 * 6) * 0.001).astype(np.float32)

    result = t.transcribe(noise, vad_filter=True)

    assert result["text"] == ""
    assert result["segments"] == []
    model.transcribe.assert_not_called()


@patch("resona_engine_whisper.transcriber.whisper")
def test_vad_decodes_only_speech_and_maps_times_back(mock_whisper):
    lead = np.zeros(16000 * 5, dtype=np.float32)  # 5 s silence before the speech
    audio = np.concatenate([lead, _speech()])
    t, model = _transcriber(
        mock_whisper, {"text": "hallo", "language": "de", "segments": [{"start": 0.0, "end": 1.0, "text": "hallo"}]}
    )

    result = t.transcribe(audio, vad_filter=True)

    decoded = model.transcribe.call_args.args[0]
    assert len(decoded) < len(audio) - 16000 * 4
    assert "vad_filter" not in model.transcribe.call_args.kwargs
    segment = result["segments"][0]
    assert 4.5 <= segment["start"] <= 5.2
    assert segment["end"] - segment["start"] == pytest.approx(1.0, abs=0.01)


@patch("resona_engine_whisper.transcriber.whisper")
def test_vad_filter_off_decodes_the_full_waveform(mock_whisper):
    t, model = _transcriber(mock_whisper, {"text": " Vielen Dank.", "segments": []})
    silence = np.zeros(16000 * 3, dtype=np.float32)

    result = t.transcribe(silence, vad_filter=False)

    assert len(model.transcribe.call_args.args[0]) == len(silence)
    assert result["text"] == " Vielen Dank."
