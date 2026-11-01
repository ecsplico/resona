"""Voice-activity filtering for Whisper engines that have no VAD of their own.

With ``vad_filter`` only the Silero-detected speech is decoded (like faster-whisper):
no speech means an empty transcript instead of Whisper's "Vielen Dank."-style
hallucinations on silence or noise. Segment and word times are mapped back to the
original audio.

Uses faster-whisper's Silero VAD, whose ONNX weights ship inside the wheel
(``faster_whisper/assets``), so private offline workers never download them. Engines
that call :func:`decode_speech` with ``vad_filter=True`` must depend on
``faster-whisper``; asr-core itself stays free of it (imported lazily).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from resona_asr_core.audio import SAMPLE_RATE
from resona_asr_core.protocol import TranscriptionResult


def speech_chunks(audio: np.ndarray, vad_parameters: dict | None) -> list[dict]:
    """Silero VAD speech spans as sample offsets."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(audio, VadOptions(**(vad_parameters or {})), sampling_rate=SAMPLE_RATE)


def restore_times(segments: list[dict], chunks: list[dict]) -> list[dict]:
    """Map segment/word times from the concatenated speech back to original-audio time."""
    from faster_whisper.vad import SpeechTimestampsMap

    ts_map = SpeechTimestampsMap(chunks, SAMPLE_RATE)
    restored_segments = []
    for segment in segments:
        segment = dict(segment)
        words = segment.get("words")
        if words:
            restored = []
            for word in words:
                word = dict(word)
                index = ts_map.get_chunk_index((word["start"] + word["end"]) / 2)
                word["start"] = ts_map.get_original_time(word["start"], index)
                word["end"] = ts_map.get_original_time(word["end"], index)
                restored.append(word)
            segment["words"] = restored
            segment["start"], segment["end"] = restored[0]["start"], restored[-1]["end"]
        else:
            segment["start"] = ts_map.get_original_time(segment["start"])
            segment["end"] = ts_map.get_original_time(segment["end"])
        restored_segments.append(segment)
    return restored_segments


def decode_speech(
    audio: np.ndarray,
    decode: Callable[[np.ndarray], dict],
    *,
    language: str,
    vad_filter: bool,
    vad_parameters: dict | None = None,
) -> TranscriptionResult:
    """Run ``decode`` (a Whisper-style ``transcribe`` returning text/language/segments).

    Without ``vad_filter`` the full waveform is decoded. With it, only detected speech
    is decoded and ``decode`` is not called at all when there is none.
    """
    if not vad_filter:
        result = decode(audio)
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", language),
            segments=result.get("segments", []) or [],
        )

    chunks = speech_chunks(audio, vad_parameters)
    if not chunks:
        return TranscriptionResult(text="", language=language, segments=[])
    speech = np.concatenate([audio[c["start"] : c["end"]] for c in chunks])
    result = decode(speech)
    return TranscriptionResult(
        text=result.get("text", ""),
        language=result.get("language", language),
        segments=restore_times(result.get("segments", []) or [], chunks),
    )


__all__ = ["decode_speech", "restore_times", "speech_chunks"]
