"""On-disk layout for organization content.

Every name is generated; client filenames never reach the filesystem::

    <DATA_PATH>/organizations/<storage_id>/
        uploads/<upload_id>.bin
        jobs/<job_id>/input.bin
        jobs/<job_id>/postprocess.json
        jobs/<job_id>/result.json
        jobs/<job_id>/attempt-<n>/

Writes stage into a temporary file in the destination directory, fsync, then
``os.replace`` into place, so a reader never observes a partial file.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from .paths import DATA_PATH

STAGING_PREFIX = ".stage-"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _safe(identifier: str) -> str:
    if not _SAFE_ID.fullmatch(identifier):
        raise ValueError("unsafe identifier for storage path")
    return identifier


def org_dir(storage_id: str) -> Path:
    return DATA_PATH / "organizations" / _safe(storage_id)


def upload_path(storage_id: str, upload_id: str) -> Path:
    return org_dir(storage_id) / "uploads" / f"{_safe(upload_id)}.bin"


def job_dir(storage_id: str, job_id: int) -> Path:
    return org_dir(storage_id) / "jobs" / str(int(job_id))


def job_input_path(storage_id: str, job_id: int) -> Path:
    return job_dir(storage_id, job_id) / "input.bin"


def postprocess_path(storage_id: str, job_id: int) -> Path:
    """Resolved postprocess profile (may contain prompts); lives as long as the input."""
    return job_dir(storage_id, job_id) / "postprocess.json"


def attempt_dir(storage_id: str, job_id: int, attempt: int) -> Path:
    return job_dir(storage_id, job_id) / f"attempt-{int(attempt)}"


def result_path(storage_id: str, job_id: int) -> Path:
    return job_dir(storage_id, job_id) / "result.json"


def staging_path(final: Path) -> Path:
    """Create an empty temporary file beside ``final`` and return its path."""
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=STAGING_PREFIX, dir=final.parent)
    os.close(fd)
    return Path(name)


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def promote(staged: Path, final: Path) -> None:
    """Atomically move a fully written staged file to its final name."""
    _fsync_file(staged)
    os.replace(staged, final)
    _fsync_file(final.parent)


def write_bytes_atomic(final: Path, data: bytes) -> None:
    staged = staging_path(final)
    try:
        staged.write_bytes(data)
        promote(staged, final)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def sweep_staging(directory: Path, *, older_than: float | None = None) -> int:
    """Delete leftover staging files in ``directory``; return how many were removed.

    ``older_than`` (epoch seconds) spares files modified after it, so a periodic
    sweep never removes a staging file that a request is still writing.
    """
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.iterdir():
        if not (entry.is_file() and entry.name.startswith(STAGING_PREFIX)):
            continue
        if older_than is not None and entry.stat().st_mtime > older_than:
            continue
        entry.unlink(missing_ok=True)
        removed += 1
    return removed
