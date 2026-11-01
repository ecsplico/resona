import sys
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from resona_asr_core.protocol import Transcriber
from resona_engine_mlx_whisper.transcriber import (
    MLXWhisperTranscriber,
    _resolve_repo,
)


def _install_fake_mlx(monkeypatch, transcribe_return):
    fake = MagicMock()
    fake.transcribe.return_value = transcribe_return
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    return fake


def test_satisfies_protocol():
    t = MLXWhisperTranscriber(modelname="tiny")
    assert isinstance(t, Transcriber)


def test_alias_resolves_to_repo():
    assert _resolve_repo("large-v3") == "mlx-community/whisper-large-v3-mlx"
    # an explicit repo passes through untouched
    assert _resolve_repo("mlx-community/whisper-tiny") == "mlx-community/whisper-tiny"


def test_transcribe_returns_expected_keys(monkeypatch):
    _install_fake_mlx(
        monkeypatch,
        {"text": "hallo welt", "language": "de", "segments": [{"text": "hallo welt"}]},
    )
    t = MLXWhisperTranscriber(modelname="large-v3")
    result = t.transcribe(np.zeros(16000, dtype=np.float32), language="de")

    assert result["text"] == "hallo welt"
    assert result["language"] == "de"
    assert result["segments"] == [{"text": "hallo welt"}]


def test_initial_prompt_and_repo_passed_through(monkeypatch):
    fake = _install_fake_mlx(monkeypatch, {"text": "", "language": "de", "segments": []})
    t = MLXWhisperTranscriber(modelname="large-v3")
    t.transcribe(np.zeros(16000), initial_prompt="Befund")

    _, kwargs = fake.transcribe.call_args
    assert kwargs["initial_prompt"] == "Befund"
    assert kwargs["path_or_hf_repo"] == "mlx-community/whisper-large-v3-mlx"


# -- vad_filter: Silero VAD (faster-whisper's bundled ONNX model, runs for real) ----

SPEECH_WAV = Path(__file__).resolve().parents[3] / "eval" / "corpus" / "de_speech.wav"


def _speech() -> np.ndarray:
    with wave.open(str(SPEECH_WAV), "rb") as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def test_vad_silence_yields_empty_transcript_without_decoding(monkeypatch):
    fake = _install_fake_mlx(monkeypatch, {"text": " Vielen Dank.", "segments": []})
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(16000 * 6) * 0.001).astype(np.float32)

    result = MLXWhisperTranscriber(modelname="large-v3").transcribe(noise, vad_filter=True)

    assert result["text"] == ""
    assert result["segments"] == []
    fake.transcribe.assert_not_called()


def test_vad_maps_segment_times_back_to_original_audio(monkeypatch):
    lead = np.zeros(16000 * 5, dtype=np.float32)  # 5 s silence before the speech
    audio = np.concatenate([lead, _speech()])
    fake = _install_fake_mlx(
        monkeypatch,
        {"text": "hallo", "language": "de", "segments": [{"start": 0.0, "end": 1.0, "text": "hallo"}]},
    )

    result = MLXWhisperTranscriber(modelname="large-v3").transcribe(audio, vad_filter=True)

    decoded = fake.transcribe.call_args.args[0]
    assert len(decoded) < len(audio) - 16000 * 4  # leading silence was not decoded
    segment = result["segments"][0]
    # speech_pad_ms=400 lets the speech span start up to 0.4 s before the 5 s mark
    assert 4.5 <= segment["start"] <= 5.2
    assert segment["end"] - segment["start"] == pytest.approx(1.0, abs=0.01)
    assert result["text"] == "hallo"


def test_vad_filter_off_decodes_the_full_waveform(monkeypatch):
    fake = _install_fake_mlx(monkeypatch, {"text": " Vielen Dank.", "segments": []})
    silence = np.zeros(16000 * 3, dtype=np.float32)

    result = MLXWhisperTranscriber(modelname="large-v3").transcribe(silence, vad_filter=False)

    assert len(fake.transcribe.call_args.args[0]) == len(silence)
    assert result["text"] == " Vielen Dank."
    assert "vad_filter" not in fake.transcribe.call_args.kwargs
