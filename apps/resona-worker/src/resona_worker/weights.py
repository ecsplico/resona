"""Pinned model weights: release pins, offline lookup and preparation-time download.

Workers never download weights while serving jobs: ``prepare`` makes every model a
profile loads resolve to the release's pinned revision (downloading it unless
``--offline``), and the execution subprocess loads the exact local copy. Pins come
from a release manifest or, by default, from ``models.json`` shipped inside this
package (the same file the release manifest is built from). Two weight stores exist:

* HuggingFace repos (``owner/name``) live in the HF hub cache; a snapshot directory
  is named by its commit hash, which is the model revision recorded in provenance.
  Engines that load a branch or tag (Docling asks for ``main`` and ``v2.3.0``) read
  ``refs/<name>`` from the cache, so preparation points that ref at the pinned commit.
* openai-whisper aliases (``large-v3``) are single checkpoints in openai-whisper's
  download root (``$XDG_CACHE_HOME/whisper``); their revision is the SHA-256 that
  openai-whisper publishes in the checkpoint URL.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from importlib.resources import files
from pathlib import Path

from pydantic import TypeAdapter, ValidationError
from resona_job_core.release import ModelPin

_CHUNK = 1 << 20
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class WeightsError(RuntimeError):
    """Pinned weights could not be fetched or do not match their pin."""


def release_model_pins() -> dict[str, str]:
    """``model -> revision`` pinned by the installed release (``resona_worker/models.json``)."""
    try:
        raw = (files("resona_worker") / "models.json").read_bytes()
        pins = TypeAdapter(list[ModelPin]).validate_json(raw)
    except (OSError, ValidationError):
        raise WeightsError("the packaged release model pins (models.json) are missing or invalid") from None
    return {pin.model: pin.revision for pin in pins}


def is_hub_repo(model: str) -> bool:
    """True for ``owner/name`` HuggingFace repo ids; short engine aliases are not repos."""
    owner, _, name = model.partition("/")
    return bool(owner and name) and "/" not in name and not model.startswith((".", "/", "~"))


def is_commit(revision: str | None) -> bool:
    return bool(revision) and _COMMIT.fullmatch(revision) is not None


def _cache_dir() -> str | None:
    # Read at call time: ``configure_model_cache`` may export HF_HUB_CACHE after
    # huggingface_hub computed its import-time default.
    return os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE") or None


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
        path = Path(
            snapshot_download(repo_id=repo, revision=revision, local_files_only=True, cache_dir=_cache_dir())
        )
    except (LocalEntryNotFoundError, HFValidationError, FileNotFoundError):
        return None
    return path if path.is_dir() else None


def download_snapshot(repo: str, revision: str) -> Path:
    """Fetch ``repo`` at ``revision`` into the HF cache (preparation only, never while serving)."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise WeightsError("huggingface_hub is not installed in this environment") from None
    try:
        return Path(snapshot_download(repo_id=repo, revision=revision, cache_dir=_cache_dir()))
    except Exception as exc:  # noqa: BLE001 - network/auth/revision errors all mean "not prepared"
        raise WeightsError(f"download of {repo}@{revision} failed: {exc.__class__.__name__}") from None


def _point_ref(snapshot: Path, ref: str, commit: str) -> None:
    """Make the cache's ``refs/<ref>`` of the repo owning ``snapshot`` name ``commit``."""
    path = snapshot.parent.parent / "refs" / ref
    try:
        if path.read_text(encoding="utf-8").strip() == commit:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(commit)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_pinned_snapshot(repo: str, ref: str | None, pin: str, *, offline: bool) -> Path:
    """Make ``repo`` at ``ref`` (what the engine loads; default ``main``) resolve to commit ``pin``.

    The pinned commit is taken from the local cache, else downloaded unless ``offline``.
    A branch or tag ``ref`` is then pointed at the pinned commit, so an engine asking for
    ``main`` offline loads exactly the release's weights. Never accepts another commit.
    """
    if not is_commit(pin):
        raise WeightsError(f"the release pins {repo} at {pin}, which is not a 40-hex commit")
    ref = ref or "main"
    if is_commit(ref) and ref != pin:
        raise WeightsError(f"{repo} is loaded at {ref}, the release pins {pin}")
    snapshot = local_snapshot(repo, pin)
    if snapshot is None:
        if offline:
            raise WeightsError(f"{repo}@{pin} is not in the local HuggingFace cache and --offline forbids downloading it")
        snapshot = download_snapshot(repo, pin)
    if not is_commit(ref):
        _point_ref(snapshot, ref, pin)
    resolved = local_snapshot(repo, ref)
    if resolved is None or resolved.name != pin:
        found = resolved.name if resolved is not None else "nothing"
        raise WeightsError(f"{repo}@{ref} resolves to {found} in the local cache, the release pins {pin}")
    return resolved


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
    "ensure_pinned_snapshot",
    "is_commit",
    "is_hub_repo",
    "is_whisper_alias",
    "local_snapshot",
    "release_model_pins",
    "sha256_file",
    "whisper_checkpoint",
    "whisper_revision",
    "whisper_root",
]
