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


def _import_asr_core(backend: str = "faster-whisper"):
    """Import asr-core's registry and audio loader. Raises ImportError with install hint."""
    try:
        from resona_asr_core.registry import get_transcriber as _get_transcriber
        from resona_asr_core.audio import load_audio as _load_audio_fn
    except ImportError as e:
        raise ImportError(
            f"{e}\n\n"
            "In-process transcription requires a backend extra. Install one:\n"
            "  uv tool install 'resona-cli[faster-whisper]'\n"
            "  uv tool install 'resona-cli[whisper]'\n"
            "  uv tool install 'resona-cli[voxtral]'"
        ) from e
    return _get_transcriber, _load_audio_fn


def get_transcriber(*args, **kwargs):
    """Re-exposed wrapper so tests can patch this symbol without touching asr-core directly."""
    fn, _ = _import_asr_core()
    return fn(*args, **kwargs)


def _load_audio(path: Path):
    """Re-exposed wrapper so tests can patch this symbol without touching asr-core directly."""
    _, fn = _import_asr_core()
    return fn(str(path))


class InProcessEngine:
    """Loads an ASR backend in-process via the resona-asr-core entry-point registry."""

    def __init__(self, backend: str = "faster-whisper") -> None:
        try:
            _import_asr_core(backend)  # fail fast with install hint if extras missing
        except ImportError as e:
            if "resona-cli[" not in str(e):
                raise ImportError(
                    f"{e}\n\n"
                    "In-process transcription requires a backend extra. Install one:\n"
                    "  uv tool install 'resona-cli[faster-whisper]'\n"
                    "  uv tool install 'resona-cli[whisper]'\n"
                    "  uv tool install 'resona-cli[voxtral]'"
                ) from e
            raise
        self._backend = backend
        self._transcriber = get_transcriber(backend)

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        samples = _load_audio(audio)
        result = self._transcriber.transcribe(samples, **kwargs)
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", ""),
            segments=result.get("segments", []),
        )
