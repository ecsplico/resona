# packages/engine-core/tests/test_audio.py
import io
import struct
import numpy as np
from unittest.mock import patch, MagicMock
from resona_engine_core.audio import load_audio, SAMPLE_RATE


def test_sample_rate_constant():
    assert SAMPLE_RATE == 16000


def test_load_audio_returns_float32():
    raw = struct.pack("<4h", 0, 16384, -16384, 0)
    f = io.BytesIO(raw)
    audio = load_audio(f, encode=False, sr=16000)
    assert audio.dtype == np.float32
    assert len(audio) == 4


@patch("resona_asr_core.audio.ffmpeg")
def test_load_audio_calls_ffmpeg(mock_ffmpeg):
    pcm = struct.pack("<2h", 0, 16384)
    mock_ffmpeg.input.return_value.output.return_value.run.return_value = (pcm, b"")
    audio = load_audio(io.BytesIO(b"fake"), encode=True, sr=16000)
    assert audio.dtype == np.float32
    mock_ffmpeg.input.assert_called_once()
