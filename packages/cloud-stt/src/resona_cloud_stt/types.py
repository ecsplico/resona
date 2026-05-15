"""Normalized result type shared by every cloud provider."""
from typing import TypedDict


class TranscriptionResult(TypedDict):
    """Return type for all cloud provider transcribe() functions.

    ``segments`` is a list of ``{start: float, end: float, text: str}`` dicts.
    """
    text: str
    language: str
    segments: list[dict]
