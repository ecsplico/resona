"""Transcriber protocol — all backends implement this interface."""

from typing import Protocol, TypedDict, runtime_checkable

import numpy as np


class TranscriptionResult(TypedDict):
    """Return type for all transcriber backends."""
    text: str
    language: str
    segments: list[dict]


@runtime_checkable
class Transcriber(Protocol):
    """Protocol that every Resona transcription backend must satisfy."""

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str = "de",
        task: str = "transcribe",
        initial_prompt: str | None = None,
        word_timestamps: bool = False,
        vad_filter: bool = False,
        **kwargs,
    ) -> TranscriptionResult: ...
