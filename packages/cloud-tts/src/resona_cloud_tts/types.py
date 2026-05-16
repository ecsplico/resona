"""Normalized result type shared by every cloud TTS provider."""
from typing import TypedDict


class SpeechResult(TypedDict):
    """Return type for all cloud provider synthesize() functions.

    ``audio`` is the raw encoded audio; ``content_type`` is its MIME type.
    """
    audio: bytes
    content_type: str
