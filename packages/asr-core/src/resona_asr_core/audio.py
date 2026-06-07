"""Audio loading utilities for Resona engine."""

import logging
from pathlib import Path
from typing import BinaryIO

import ffmpeg
import numpy as np

SAMPLE_RATE = 16000
log = logging.getLogger(__name__)

_EMPTY_AUDIO_HINT = (
    "ffmpeg decoded 0 audio samples. This commonly happens with non-faststart "
    "MP4/M4A files (e.g. iOS Voice Memos) whose 'moov' index atom sits after the "
    "media data: decoding from a non-seekable stream then fails to find the "
    "stream index. Decode from the file path instead (load_audio_path), or "
    "rewrite the file with `ffmpeg -i in.m4a -c copy -movflags +faststart out.m4a`."
)


def _pcm_to_waveform(out: bytes, sr: int) -> np.ndarray:
    """Convert raw s16le PCM bytes to a mono float32 waveform, guarding emptiness."""
    if not out:
        log.error(_EMPTY_AUDIO_HINT)
        raise RuntimeError(_EMPTY_AUDIO_HINT)
    waveform = np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0
    log.info(f"Audio loaded successfully. Shape: {waveform.shape}, Sample Rate: {sr}")
    return waveform


def load_audio(file: BinaryIO, encode: bool = True, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Read an audio file object as mono float32 waveform, resampling as necessary.

    Decodes from an in-memory stream (ffmpeg ``pipe:``). Non-seekable input means
    container formats whose index lives at the end of the file (non-faststart
    MP4/M4A) cannot be decoded — prefer :func:`load_audio_path` when a path is
    available. Raises ``RuntimeError`` if decoding yields no samples.
    """
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

    return _pcm_to_waveform(out, sr)


def load_audio_path(path: str | Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Read an audio file by path as mono float32 waveform, resampling as necessary.

    Decodes from the real file path so ffmpeg has a seekable input. This handles
    non-faststart MP4/M4A files (e.g. iOS Voice Memos) that :func:`load_audio`
    cannot, because their trailing 'moov' atom requires seeking. Raises
    ``RuntimeError`` if decoding yields no samples.
    """
    try:
        out, _ = (
            ffmpeg.input(str(path), threads=0)
            .output("-", format="s16le", acodec="pcm_s16le", ac=1, ar=sr)
            .run(cmd="ffmpeg", capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as e:
        stderr = e.stderr.decode()
        log.error(f"FFmpeg error during audio loading: {stderr}")
        raise RuntimeError(f"Failed to load audio: {stderr}") from e
    except Exception as e:
        log.error(f"Unexpected error during audio loading: {e}")
        raise RuntimeError(f"Failed to load audio: {e}") from e

    return _pcm_to_waveform(out, sr)
