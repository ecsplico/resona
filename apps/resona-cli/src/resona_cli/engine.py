"""Engine abstraction — uniform interface for remote (HTTP) and in-process transcription.

The Engine Protocol defines the contract: accept an audio Path, return a
TranscriptionResult dict with at least ``text``, ``language``, and ``segments``.
"""
import os
from pathlib import Path
from typing import Protocol, TypedDict, runtime_checkable

from resona_client.client import ResonaClient
from resona_client.config import EngineEntry


class TranscriptionResult(TypedDict):
    text: str
    language: str
    segments: list


@runtime_checkable
class Engine(Protocol):
    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult: ...


class RemoteEngine:
    """Submits a single job to a resona-api server and waits for the result.

    Provides a synchronous, single-file interface for programmatic users. The
    `resona transcribe` CLI command does NOT use this class — it talks to
    `ResonaClient` directly so it can submit a batch of jobs concurrently and
    then wait for all results. This class is for callers who want a uniform
    `Engine` interface (matching `InProcessEngine`) for one-shot transcription.
    """

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


_INSTALL_HINT = (
    "In-process transcription requires resona-asr-core, which ships with the\n"
    "base resona-cli install. Reinstall it, or for the PyTorch engines add an extra:\n"
    "  uv tool install --reinstall --from ./apps/resona-cli resona-cli\n"
    "  uv tool install 'resona-cli[whisper]'\n"
    "  uv tool install 'resona-cli[voxtral]'"
)


def _import_asr_core():
    """Import asr-core's registry and audio loader. Raises ImportError with install hint."""
    try:
        from resona_asr_core.registry import get_transcriber as _get_transcriber
        from resona_asr_core.audio import load_audio_path as _load_audio_fn
    except ImportError as e:
        raise ImportError(f"{e}\n\n{_INSTALL_HINT}") from e
    return _get_transcriber, _load_audio_fn


def get_transcriber(*args, **kwargs):
    """Re-exposed wrapper so tests can patch this symbol without touching asr-core directly."""
    fn, _ = _import_asr_core()
    return fn(*args, **kwargs)


def _load_audio(path: Path):
    """Re-exposed wrapper so tests can patch this symbol without touching asr-core directly.

    Uses asr-core's path-based loader so ffmpeg gets a seekable input — this
    handles non-faststart MP4/M4A files (e.g. iOS Voice Memos) that the
    stream-based loader cannot decode.
    """
    _, fn = _import_asr_core()
    return fn(path)


class InProcessEngine:
    """Loads an ASR engine in-process via the resona-asr-core entry-point registry."""

    def __init__(self, engine: str = "faster-whisper") -> None:
        try:
            _import_asr_core()  # fail fast with install hint if extras missing
        except ImportError as e:
            if "resona-cli[" not in str(e):
                raise ImportError(f"{e}\n\n{_INSTALL_HINT}") from e
            raise
        self._engine = engine
        self._transcriber = get_transcriber(engine)

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        samples = _load_audio(audio)
        result = self._transcriber.transcribe(samples, **kwargs)
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", ""),
            segments=result.get("segments", []),
        )


class CloudEngine:
    """Engine that transcribes via a cloud STT provider (resona-cloud-stt).

    Wraps a resolved ``cloud`` :class:`EngineEntry`. The provider's API key is
    read from the environment variable named in
    ``resona_cloud_stt.registry.PROVIDER_ENV_KEYS``. The ``transcribe``
    keyword arguments ``model`` and ``language`` override the entry's values
    for that run.
    """

    def __init__(self, entry: EngineEntry) -> None:
        if entry.type != "cloud":
            raise ValueError(
                f"CloudEngine requires a cloud engine entry, got type {entry.type!r}"
            )
        self._entry = entry

    def transcribe(self, audio: Path, **kwargs) -> TranscriptionResult:
        from resona_cloud_stt.errors import MissingAPIKeyError
        from resona_cloud_stt.registry import PROVIDER_ENV_KEYS, get_provider

        provider = self._entry.provider or ""
        env_var = PROVIDER_ENV_KEYS.get(provider)
        api_key = os.getenv(env_var) if env_var else None
        if not api_key:
            raise MissingAPIKeyError(env_var or f"<unknown provider {provider!r}>")

        model = kwargs.get("model") or self._entry.model
        language = kwargs.get("language") or None
        result = get_provider(provider).transcribe(
            Path(audio),
            api_key=api_key,
            model=model,
            language=language,
            options=self._entry.options or None,
        )
        return TranscriptionResult(
            text=result.get("text", ""),
            language=result.get("language", ""),
            segments=result.get("segments", []),
        )
