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


class StreamUpdate(TypedDict):
    """Incremental result emitted by a native streaming session.

    ``confirmed_delta`` is the *new* stable text since the previous update
    (never the cumulative transcript); ``partial`` is the current unstable tail.
    """
    confirmed_delta: str
    partial: str
    language: str


@runtime_checkable
class StreamSession(Protocol):
    """A single live audio stream handed to a native streaming engine.

    The session is stateful: it owns whatever rolling context the engine needs.
    Audio is mono float32 at :data:`resona_asr_core.audio.SAMPLE_RATE` (16 kHz).
    """

    def feed(self, audio: np.ndarray) -> "StreamUpdate | None":
        """Push more audio; return an update if new text is available, else None."""
        ...

    def flush(self) -> StreamUpdate:
        """Finalize the stream; return any remaining confirmed text."""
        ...


@runtime_checkable
class StreamingTranscriber(Protocol):
    """Optional capability: engines that decode incrementally with low latency.

    When a registered engine satisfies this protocol, :class:`LiveTranscriber`
    delegates to a native :class:`StreamSession` instead of re-transcribing
    overlapping windows with local agreement. Engines that only implement
    :class:`Transcriber` still work via the windowed fallback.
    """

    def stream_session(
        self, *, language: str = "de", task: str = "transcribe"
    ) -> StreamSession: ...
