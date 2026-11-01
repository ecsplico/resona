"""Offline lookup and explicit download of prepared model weights.

Workers never download weights while serving jobs: ``prepare --download`` fetches
the pinned weights, ``prepare`` verifies them, and the execution subprocess loads
the exact local copy. Two weight stores exist:

* HuggingFace repos (``owner/name``) live in the HF hub cache; a snapshot directory
  is named by its commit hash, which is the model revision recorded in provenance.
* openai-whisper aliases (``large-v3``) are single checkpoints in openai-whisper's
  download root (``$XDG_CACHE_HOME/whisper``); their revision is the SHA-256 that
  openai-whisper publishes in the checkpoint URL.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_CHUNK = 1 << 20


class WeightsError(RuntimeError):
    """Pinned weights could not be fetched or do not match their pin."""


def is_hub_repo(model: str) -> bool:
    """True for ``owner/name`` HuggingFace repo ids; short engine aliases are not repos."""
    owner, _, name = model.partition("/")
    return bool(owner and name) and "/" not in name and not model.startswith((".", "/", "~"))


def local_snapshot(repo: str, revision: str | None) -> Path | None:
    """Return the cached snapshot of ``repo`` at ``revision`` (branch, tag or commit; default main).

    Uses the local cache only; returns ``None`` when the weights are not prepared
    or ``huggingface_hub`` is not installed in this environment.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import HFValidationError, LocalEntryNotFoundError
    except ImportError:
        return None
    try:
        path = Path(snapshot_download(repo_id=repo, revision=revision, local_files_only=True))
    except (LocalEntryNotFoundError, HFValidationError, FileNotFoundError):
        return None
    return path if path.is_dir() else None


def download_snapshot(repo: str, revision: str | None) -> Path:
    """Fetch ``repo`` at ``revision`` into the HF cache (preparation only, never while serving)."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise WeightsError("huggingface_hub is not installed in this environment") from None
    try:
        return Path(snapshot_download(repo_id=repo, revision=revision))
    except Exception as exc:  # noqa: BLE001 - network/auth/revision errors all mean "not prepared"
        raise WeightsError(f"download of {repo} failed: {exc.__class__.__name__}") from None


def _whisper_models() -> dict[str, str]:
    try:
        import whisper
    except ImportError:
        return {}
    return dict(getattr(whisper, "_MODELS", {}))


def is_whisper_alias(engine: str, model: str) -> bool:
    """True when ``model`` is an openai-whisper checkpoint name served by the ``whisper`` engine."""
    return engine == "whisper" and not is_hub_repo(model) and model in _whisper_models()


def whisper_root() -> Path:
    """openai-whisper's default download root (honours ``XDG_CACHE_HOME`` like the library)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "whisper"


def whisper_revision(model: str) -> str:
    """SHA-256 openai-whisper publishes for ``model`` (the URL's second-to-last path segment)."""
    url = _whisper_models().get(model)
    if url is None:
        raise WeightsError(f"{model} is not an openai-whisper checkpoint")
    return url.rstrip("/").split("/")[-2]


def whisper_checkpoint(model: str) -> Path | None:
    """Local checkpoint file of an openai-whisper alias, or ``None`` when not prepared (no hashing)."""
    url = _whisper_models().get(model)
    if url is None:
        return None
    path = whisper_root() / Path(url).name
    return path if path.is_file() and not path.is_symlink() else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download_whisper_checkpoint(model: str) -> Path:
    """Fetch an openai-whisper checkpoint into ``whisper_root()``; the library verifies its SHA-256."""
    url = _whisper_models().get(model)
    if url is None:
        raise WeightsError(f"{model} is not an openai-whisper checkpoint")
    import whisper

    try:
        return Path(whisper._download(url, str(whisper_root()), in_memory=False))  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - network or checksum failure
        raise WeightsError(f"download of whisper {model} failed: {exc.__class__.__name__}") from None


__all__ = [
    "WeightsError",
    "download_snapshot",
    "download_whisper_checkpoint",
    "is_hub_repo",
    "is_whisper_alias",
    "local_snapshot",
    "sha256_file",
    "whisper_checkpoint",
    "whisper_revision",
    "whisper_root",
]
