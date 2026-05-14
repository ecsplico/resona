"""Back-compat shim — LiveTranscriber now lives in resona-asr-core."""
from resona_asr_core.live_transcriber import (  # noqa: F401
    LiveTranscriber,
    TranscriptionResult,
    SAMPLE_RATE,
    MIN_CHUNK_SECONDS,
    MAX_BUFFER_SECONDS,
    MAX_TRANSCRIBE_SECONDS,
    OVERLAP_SECONDS,
    MIN_NEW_AUDIO_SECONDS,
    MAX_STALE_CYCLES,
)

__all__ = [
    "LiveTranscriber",
    "TranscriptionResult",
    "SAMPLE_RATE",
    "MIN_CHUNK_SECONDS",
    "MAX_BUFFER_SECONDS",
    "MAX_TRANSCRIBE_SECONDS",
    "OVERLAP_SECONDS",
    "MIN_NEW_AUDIO_SECONDS",
    "MAX_STALE_CYCLES",
]
