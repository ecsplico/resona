"""Normalized result type shared by every local TTS engine."""
from typing import TypedDict


class SpeechResult(TypedDict):
    """Return type for every local engine's ``synthesize()``.

    ``audio`` is encoded audio bytes (WAV/PCM_16 by default); ``content_type``
    is its MIME type; ``sample_rate`` is the native rate of the audio so callers
    can resample if needed. Shape-compatible with
    ``resona_cloud_tts.types.SpeechResult`` (plus ``sample_rate``).
    """

    audio: bytes
    content_type: str
    sample_rate: int
