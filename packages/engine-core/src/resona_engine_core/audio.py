"""Audio loading utilities for Resona engine."""

import logging
from typing import BinaryIO

import ffmpeg
import numpy as np

SAMPLE_RATE = 16000
log = logging.getLogger(__name__)


def load_audio(file: BinaryIO, encode: bool = True, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Read an audio file object as mono float32 waveform, resampling as necessary."""
    if encode:
        try:
            out, _ = (
                ffmpeg.input("pipe:", threads=0)
                .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=sr)
                .run(cmd="ffmpeg", capture_stdout=True, capture_stderr=True, input=file.read())
            )
        except ffmpeg.Error as e:
            stderr = e.stderr.decode()
            log.error(f"FFmpeg error during audio loading: {stderr}")
            raise RuntimeError(f"Failed to load audio: {stderr}") from e
        except Exception as e:
            log.error(f"Unexpected error during audio loading: {e}")
            raise RuntimeError(f"Failed to load audio: {e}") from e
    else:
        out = file.read()

    waveform = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0
    log.info(f"Audio loaded successfully. Shape: {waveform.shape}, Sample Rate: {sr}")
    return waveform
