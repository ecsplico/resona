"""Engine abstraction — uniform interface for remote (HTTP) and in-process transcription.

The Engine Protocol defines the contract: accept an audio Path, return a
TranscriptionResult dict with at least ``text``, ``language``, and ``segments``.
"""
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from resona_client.client import ResonaClient


class TranscriptionResult(TypedDict):
    text: str
    language: str
    segments: list


@runtime_checkable
class Engine(Protocol):
    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult: ...


class RemoteEngine:
    """Submits jobs to a resona-api server and waits for the result."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        if base_url:
            self._client = ResonaClient(base_url=base_url, api_key=api_key)
        else:
            self._client = ResonaClient.from_config()

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        job = self._client.submit_job(audio)
        result = self._client.wait_for_job(job["id"])
        return TranscriptionResult(
            text=result.get("md") or result.get("transcript", ""),
            language=result.get("language", ""),
            segments=result.get("segments", []),
        )
