"""Engine discovery and singleton management via Python entry points."""

import logging
from importlib.metadata import entry_points
from threading import Lock

from decouple import config

from .protocol import Transcriber

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None
_init_lock = Lock()

ENTRY_POINT_GROUP = "resona.engines"


def _detect_device() -> str:
    """Return 'cuda' if a GPU is available, else 'cpu'.

    Prefers torch when present (whisper / voxtral engines ship it); otherwise
    falls back to CTranslate2 (the faster-whisper engine ships it). With
    neither installed, assumes CPU.
    """
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        pass
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"


def _load_from_entrypoint(engine: str | None = None) -> Transcriber:
    """Discover and instantiate a transcriber engine by name."""
    name = engine or config("RESONA_ENGINE", default="faster-whisper")
    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name == name:
            cls = ep.load()
            device = _detect_device()
            log.info(f"Loading engine '{name}' on {device}")
            instance = cls(device=device)
            assert isinstance(instance, Transcriber), (
                f"Engine '{name}' does not satisfy the Transcriber protocol"
            )
            log.info(f"Engine '{name}' ready.")
            return instance

    installed = [ep.name for ep in eps]
    raise ValueError(
        f"Engine '{name}' not found. Installed engines: {installed}"
    )


def get_transcriber(engine: str | None = None) -> Transcriber:
    """Return the singleton transcriber, creating it on first call.

    The `engine` argument selects which engine to load on the very first call.
    On subsequent calls the singleton is returned and `engine` is **ignored**.
    Call `reset()` first if you need to swap engines (intended for tests only).
    """
    global _transcriber
    if _transcriber is None:
        with _init_lock:
            if _transcriber is None:
                _transcriber = _load_from_entrypoint(engine)
    return _transcriber


def reset() -> None:
    """Reset the singleton (for testing only)."""
    global _transcriber
    _transcriber = None
