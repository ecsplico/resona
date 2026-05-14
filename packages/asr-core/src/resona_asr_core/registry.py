"""Backend discovery and singleton management via Python entry points."""

import logging
from importlib.metadata import entry_points
from threading import Lock

from decouple import config

from .protocol import Transcriber

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None
_init_lock = Lock()

ENTRY_POINT_GROUP = "resona.backends"


def _detect_device() -> str:
    """Return 'cuda' if available, else 'cpu'."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _load_from_entrypoint(backend: str | None = None) -> Transcriber:
    """Discover and instantiate a transcriber backend by name."""
    name = backend or config("RESONA_BACKEND", default="faster-whisper")
    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name == name:
            cls = ep.load()
            device = _detect_device()
            log.info(f"Loading backend '{name}' on {device}")
            instance = cls(device=device)
            assert isinstance(instance, Transcriber), (
                f"Backend '{name}' does not satisfy the Transcriber protocol"
            )
            log.info(f"Backend '{name}' ready.")
            return instance

    installed = [ep.name for ep in eps]
    raise ValueError(
        f"Backend '{name}' not found. Installed backends: {installed}"
    )


def get_transcriber(backend: str | None = None) -> Transcriber:
    """Return the singleton transcriber, creating it on first call."""
    global _transcriber
    if _transcriber is None:
        with _init_lock:
            if _transcriber is None:
                _transcriber = _load_from_entrypoint(backend)
    return _transcriber


def reset() -> None:
    """Reset the singleton (for testing only)."""
    global _transcriber
    _transcriber = None
