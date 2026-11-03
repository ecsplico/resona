"""Hub maintenance: lease expiry, content retention and crash recovery.

The hub runs no inference. :func:`maintenance_loop` calls :func:`run_once`
periodically (and once at startup, which doubles as crash recovery):

* expired leases are requeued or failed, pending jobs past their deadline fail
  and lose their input (:func:`broker.sweep_leases`);
* unattached uploads are deleted once ``expires_at`` passes (one hour);
* result files are deleted once ``result_expires_at`` passes (24 hours); the job
  row and its hash-only ``result_manifest`` stay, so the result route answers
  410 ``result_expired`` from ``result_expires_at`` alone;
* terminal job rows with their job directory, consumed upload rows and
  idempotency records are deleted seven days after completion/creation;
* staging files untouched for an hour, abandoned attempt directories, inputs or
  unaccepted results left behind by terminal jobs, and orphaned job directories
  are removed.

Only counts are logged, never content, filenames or identifiers of content.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from . import broker, clock, storage
from .db import engine as db_engine
from .db.models import IdempotencyRecord, Job, JobStatus, Organization, Upload, UploadLink

log = logging.getLogger(__name__)

METADATA_TTL = timedelta(days=7)
STAGING_GRACE = timedelta(hours=1)
INTERVAL_SECONDS = 30.0
_TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
_ATTEMPT_PREFIX = "attempt-"

last_tick: datetime | None = None
"""When the running loop last finished a pass without error (``/ready`` reads it)."""


def _expired(value: datetime | None, now: datetime) -> bool:
    return value is not None and clock.as_utc(value) <= now


def _storage_ids(session: Session) -> dict[str, str]:
    return {org.id: org.storage_id for org in session.exec(select(Organization)).all()}


def _expire_uploads(session: Session, storage_ids: dict[str, str], now: datetime) -> int:
    expired: list[Path] = []
    # Under the write lock, so a job creation cannot consume an upload being deleted.
    with broker._immediate(session):
        for upload in session.exec(select(Upload).where(Upload.consumed_by_job_id.is_(None))).all():
            if _expired(upload.expires_at, now):
                expired.append(storage.upload_path(storage_ids[upload.organization_id], upload.id))
                session.delete(upload)
    for path in expired:
        storage.remove_file(path)
    return len(expired)


def _expire_upload_links(session: Session, now: datetime) -> int:
    """Delete links past their expiry, used or not; a used link is already spent."""
    removed = 0
    with broker._immediate(session):
        for link in session.exec(select(UploadLink)).all():
            if _expired(link.expires_at, now):
                session.delete(link)
                removed += 1
    return removed


def _expire_results(session: Session, storage_ids: dict[str, str], now: datetime) -> int:
    removed = 0
    jobs = session.exec(select(Job).where(Job.status == JobStatus.COMPLETED)).all()
    session.commit()
    for job in jobs:
        if not _expired(job.result_expires_at, now):
            continue
        path = storage.result_path(storage_ids[job.organization_id], job.id)
        if path.exists():
            storage.remove_file(path)
            removed += 1
    return removed


def _delete_metadata(session: Session, storage_ids: dict[str, str], now: datetime) -> int:
    cutoff = now - METADATA_TTL
    job_dirs: list[Path] = []
    upload_files: list[Path] = []
    deleted = 0
    with broker._immediate(session):
        for job in session.exec(select(Job).where(Job.status.in_(_TERMINAL))).all():
            if _expired(job.completed_at or job.updated_at, cutoff):
                job_dirs.append(storage.job_dir(storage_ids[job.organization_id], job.id))
                session.delete(job)
                deleted += 1
        consumed = select(Upload).where(Upload.consumed_by_job_id.is_not(None))
        for upload in session.exec(consumed).all():
            if _expired(upload.created_at, cutoff):
                upload_files.append(storage.upload_path(storage_ids[upload.organization_id], upload.id))
                session.delete(upload)
                deleted += 1
        for record in session.exec(select(IdempotencyRecord)).all():
            if _expired(record.created_at, cutoff):
                session.delete(record)
                deleted += 1
    for path in job_dirs:
        storage.remove_tree(path)
    for path in upload_files:
        storage.remove_file(path)
    return deleted


def _stale(path: Path, stale_before: float) -> bool:
    try:
        return path.stat().st_mtime <= stale_before
    except FileNotFoundError:
        return False


def _reconcile_job_dir(directory: Path, job: Job | None, stale_before: float) -> int:
    """Remove what a job directory must no longer hold; return the number of entries removed."""
    if job is None:
        # Orphan: its row was deleted or never committed. Spare a fresh directory
        # whose row may still be committing.
        if not _stale(directory, stale_before):
            return 0
        storage.remove_tree(directory)
        return 1
    removed = 0
    for entry in directory.iterdir():
        if not (entry.is_dir() and entry.name.startswith(_ATTEMPT_PREFIX)):
            continue
        number = entry.name[len(_ATTEMPT_PREFIX):]
        if not number.isdigit():
            continue
        # ``job`` is a snapshot: a later claim may already have started a higher
        # attempt, so only attempts at or below the snapshot's are judged.
        attempt = int(number)
        if attempt < job.attempt or (attempt == job.attempt and job.status != JobStatus.PROCESSING):
            storage.remove_tree(entry)
            removed += 1
    if job.status in _TERMINAL:
        leftovers = [directory / "input.bin"]
        if job.status != JobStatus.COMPLETED:
            leftovers.append(directory / "result.json")  # promoted, never committed
        for path in leftovers:
            if path.exists():
                storage.remove_file(path)
                removed += 1
    return removed


def _sweep_files(session: Session, storage_ids: dict[str, str], now: datetime) -> tuple[int, int]:
    """Return ``(stale staging files, reconciled job entries)`` removed from disk."""
    stale_before = (now - STAGING_GRACE).timestamp()
    jobs = {job.id: job for job in session.exec(select(Job)).all()}
    session.commit()
    staging = reconciled = 0
    for storage_id in storage_ids.values():
        root = storage.org_dir(storage_id)
        staging += storage.sweep_staging(root / "uploads", older_than=stale_before)
        jobs_root = root / "jobs"
        if not jobs_root.is_dir():
            continue
        for directory in jobs_root.iterdir():
            if not (directory.is_dir() and directory.name.isdigit()):
                continue
            job = jobs.get(int(directory.name))
            if job is not None and storage_ids.get(job.organization_id) != storage_id:
                job = None
            staging += storage.sweep_staging(directory, older_than=stale_before)
            reconciled += _reconcile_job_dir(directory, job, stale_before)
    return staging, reconciled


def run_once() -> dict[str, int]:
    """Run one maintenance pass; return how many items each step changed."""
    with Session(db_engine.engine) as session:
        leases = broker.sweep_leases(session)
        now = clock.now()
        storage_ids = _storage_ids(session)
        session.commit()
        uploads = _expire_uploads(session, storage_ids, now)
        links = _expire_upload_links(session, now)
        results = _expire_results(session, storage_ids, now)
        metadata = _delete_metadata(session, storage_ids, now)
        staging, reconciled = _sweep_files(session, storage_ids, now)
    return {
        "leases_swept": leases,
        "uploads_expired": uploads,
        "upload_links_expired": links,
        "results_expired": results,
        "metadata_deleted": metadata,
        "staging_swept": staging,
        "files_reconciled": reconciled,
    }


async def maintenance_loop(interval: float = INTERVAL_SECONDS) -> None:
    """Run :func:`run_once` every ``interval`` seconds until cancelled; record each successful pass."""
    global last_tick
    while True:
        try:
            counts = await asyncio.to_thread(run_once)
            last_tick = clock.now()
            changed = {name: count for name, count in counts.items() if count}
            if changed:
                log.info("Maintenance: %s", changed)
        except Exception:
            log.exception("Maintenance pass failed")
        await asyncio.sleep(interval)


__all__ = ["INTERVAL_SECONDS", "METADATA_TTL", "STAGING_GRACE", "last_tick", "maintenance_loop", "run_once"]
