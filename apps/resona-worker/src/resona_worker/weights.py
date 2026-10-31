"""Offline lookup of prepared model weights in the local HuggingFace cache.

Workers never download weights while serving jobs: ``prepare`` verifies them and
the execution subprocess loads the exact local snapshot. A snapshot directory is
named by its commit hash, which is the model revision recorded in provenance.
"""

from __future__ import annotations

from pathlib import Path


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


__all__ = ["is_hub_repo", "local_snapshot"]
