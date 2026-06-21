"""Engine discovery and singleton management via Python entry points."""

import logging
import platform
import sys
from importlib.metadata import entry_points
from threading import Lock

from decouple import config

from .model_cache import configure_model_cache
from .protocol import Transcriber

log = logging.getLogger(__name__)

_transcriber: Transcriber | None = None
_init_lock = Lock()

ENTRY_POINT_GROUP = "resona.engines"

# Per-environment default engine preference, informed by the benchmark suite
# (see benchmarks/). Each tuple is tried in order; the first installed engine
# wins. RESONA_ENGINE always overrides this.
#
# Apple Silicon: CTranslate2 (faster-whisper) is CPU-only, so the GPU-native
# engines are several times faster at the same model size. mlx-whisper gives the
# best speed+accuracy balance, then the batched lightning-mlx, then whisper.cpp.
_APPLE_SILICON_PRIORITY = ("mlx-whisper", "lightning-mlx", "whisper-cpp", "faster-whisper")
# CUDA / CPU Linux: faster-whisper (CTranslate2) uses the GPU when present and
# the CPU otherwise — a solid default on both. (To be benchmarked on Linux.)
_GENERIC_PRIORITY = ("faster-whisper", "whisper-cpp", "whisper", "voxtral")


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def installed_engines() -> list[str]:
    """Names of all engines registered via the `resona.engines` entry points."""
    return [ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)]


def recommended_engine(installed: list[str] | None = None) -> str:
    """Best default engine for the current environment, limited to installed ones.

    Apple Silicon prefers the GPU-native engines (mlx-whisper first); elsewhere
    faster-whisper is preferred. Falls back to any installed engine, or the
    `faster-whisper` name if nothing is discoverable.
    """
    available = set(installed if installed is not None else installed_engines())
    priority = _APPLE_SILICON_PRIORITY if _is_apple_silicon() else _GENERIC_PRIORITY
    for name in priority:
        if name in available:
            return name
    return next(iter(sorted(available)), "faster-whisper")


def _detect_device() -> str:
    """Return 'cuda', 'mps', or 'cpu' based on what's available.

    Prefers torch when present (whisper / voxtral engines ship it); otherwise
    falls back to CTranslate2 (the faster-whisper engine ships it). With
    neither installed, assumes CPU.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    except ImportError:
        pass
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except ImportError:
        return "cpu"


def _load_from_entrypoint(engine: str | None = None) -> Transcriber:
    """Discover and instantiate a transcriber engine by name."""
    # Share the model cache (with Voicebox etc.) before any engine imports a
    # model library — see model_cache.configure_model_cache.
    configure_model_cache()
    eps = list(entry_points(group=ENTRY_POINT_GROUP))
    # Priority: explicit arg > RESONA_ENGINE env > environment-aware default.
    name = engine or config("RESONA_ENGINE", default="") or recommended_engine(
        [ep.name for ep in eps]
    )
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
