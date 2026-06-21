"""Shared HuggingFace model-cache configuration.

Aligns Resona's on-disk model cache with **Voicebox** (and any other
HuggingFace-based tool) so large weights are shared rather than downloaded
twice. Both default to the HuggingFace hub cache (``~/.cache/huggingface/hub``
on macOS/Linux); a custom location set via ``RESONA_MODELS_DIR`` or Voicebox's
``VOICEBOX_MODELS_DIR`` is honored and exported to ``HF_HUB_CACHE`` so MLX,
``transformers`` and ``huggingface_hub`` all resolve to the same folder.

This mirrors Voicebox's ``backend/config.py`` (``VOICEBOX_MODELS_DIR`` →
``HF_HUB_CACHE``). Importing and calling :func:`configure_model_cache` early —
before any engine imports a model library — is enough to share the cache.
"""

import logging
import os
from pathlib import Path

from decouple import config

log = logging.getLogger(__name__)

_configured = False


def configure_model_cache() -> str:
    """Point HuggingFace caches at the shared models dir; return its path.

    Resolution order for the base directory:

    1. ``RESONA_MODELS_DIR`` (Resona's own override)
    2. ``VOICEBOX_MODELS_DIR`` (share Voicebox's downloaded models)
    3. an already-set ``HF_HUB_CACHE`` / ``HF_HOME``
    4. the HuggingFace default (``~/.cache/huggingface/hub``)

    Idempotent — safe to call from every engine on startup.
    """
    global _configured
    custom = (
        config("RESONA_MODELS_DIR", default="")
        or config("VOICEBOX_MODELS_DIR", default="")
    )
    if custom:
        path = str(Path(custom).expanduser().resolve())
        # HF_HUB_CACHE is the hub snapshot store; set the legacy alias too so
        # older huggingface_hub / transformers builds agree on the location.
        os.environ["HF_HUB_CACHE"] = path
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", path)
        if not _configured:
            log.info("Model cache shared at: %s", path)
    _configured = True
    return model_cache_dir()


def model_cache_dir() -> str:
    """Return the effective HuggingFace hub cache directory."""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    try:
        from huggingface_hub import constants

        return constants.HF_HUB_CACHE
    except Exception:  # noqa: BLE001 — hub not installed; fall back to default
        return str(Path.home() / ".cache" / "huggingface" / "hub")


_WEIGHT_EXTS = (".safetensors", ".bin", ".npz", ".gguf", ".pt", ".pth")


def is_model_cached(
    repo_id: str,
    *,
    weight_extensions: tuple[str, ...] = _WEIGHT_EXTS,
    required_files: list[str] | None = None,
) -> bool:
    """Whether ``repo_id`` is fully present in the shared cache.

    Mirrors Voicebox's ``is_model_cached``: an in-progress download (any
    ``*.incomplete`` blob) counts as *not* cached, and at least one weight file
    (or every ``required_files`` entry) must be present in a snapshot.
    """
    repo_cache = Path(model_cache_dir()) / ("models--" + repo_id.replace("/", "--"))
    if not repo_cache.exists():
        return False
    blobs = repo_cache / "blobs"
    if blobs.exists() and any(blobs.glob("*.incomplete")):
        return False
    snapshots = repo_cache / "snapshots"
    if not snapshots.exists():
        return False
    if required_files:
        return all(any(snapshots.rglob(name)) for name in required_files)
    return any(any(snapshots.rglob(f"*{ext}")) for ext in weight_extensions)
