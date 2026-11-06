"""In-process transcription for the `live` TUI: `InProcessEngine` wraps an
resona-asr-core transcriber and returns a TranscriptionResult dict with at
least ``text``, ``language``, and ``segments``.
"""
from pathlib import Path
from typing import TypedDict


class TranscriptionResult(TypedDict):
    text: str
    language: str
    segments: list


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
