"""Offline restore of a hub database backup: ``resona-admin restore <backup>``.

A backup is older than the hub it replaces, so it can hold credentials that were
revoked after it was taken, leases that no longer exist and references to payload
files that were never backed up (payload directories are transient by design).
Restoring therefore never trusts the restored identities or content:

1. refuse while the hub runs (hub lock held) or the target database is locked;
2. copy the backup over the configured database with the SQLite backup API, then
   migrate it to the current schema;
3. revoke **every** credential and worker in the restored database (operators
   issue fresh ones afterwards), so a credential revoked after the backup was
   taken can never come back;
4. invalidate every active lease: processing jobs are requeued, or failed when no
   attempt or time is left; cancel-requested ones end as cancelled;
5. jobs whose payload files are gone become ``content_unavailable``: pending jobs
   without input (or without their resolved postprocess profile) fail with that
   code; completed jobs without a result file keep their status but their result
   route answers 410 ``content_unavailable``. Results are never fabricated.
   Unconsumed uploads without their file are dropped;
6. run one maintenance pass at the current time, which reapplies content and
   metadata retention before any request is served.

Only counts are reported, never identifiers of content.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session, select

from . import broker, clock, hublock, maintenance, storage
from .db import engine as db_engine
from .db.models import Credential, Job, JobStatus, Organization, Upload, Worker
from .db.schema import TARGET_VERSION
from .jobs_routes import CONTENT_UNAVAILABLE

_SIDECARS = ("-wal", "-shm", "-journal")


class RestoreError(RuntimeError):
    """The backup cannot be restored safely; nothing was changed."""


@dataclass
class RestoreReport:
    credentials_revoked: int = 0
    workers_revoked: int = 0
    leases_invalidated: int = 0
    inputs_unavailable: int = 0
    results_unavailable: int = 0
    uploads_dropped: int = 0
    maintenance: dict[str, int] = field(default_factory=dict)


def _target_database() -> Path:
    database = db_engine.engine.url.database
    if not database or database == ":memory:":
        raise RestoreError("the configured database is not a file")
    return Path(database).resolve()


def _check_backup(backup: Path) -> None:
    if not backup.is_file():
        raise RestoreError("backup file not found")
    conn = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RestoreError("backup failed the SQLite integrity check")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "schema_version" not in tables:
            raise RestoreError("backup has no schema_version table; not a hub database")
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"backup is not a readable SQLite database: {exc}") from None
    finally:
        conn.close()
    if version > TARGET_VERSION:
        raise RestoreError(f"backup schema {version} is newer than this hub ({TARGET_VERSION})")


def _check_unlocked(target: Path) -> None:
    """Refuse when another process holds the target database (a hub without the lock file)."""
    if not target.exists():
        return
    conn = sqlite3.connect(target, timeout=0, isolation_level=None)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        raise RestoreError("the hub database is locked; stop the hub first") from None
    finally:
        conn.close()


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_database(backup: Path, target: Path) -> None:
    staged = target.with_name(f"{target.name}.restore")
    staged.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    dst = sqlite3.connect(staged)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file to promote
    finally:
        dst.close()
        src.close()
    db_engine.engine.dispose()
    for suffix in _SIDECARS:
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    os.replace(staged, target)
    _fsync_dir(target.parent)


def _has_postprocess(job: Job) -> bool:
    return bool(json.loads(job.execution_snapshot or "{}").get("postprocess"))


def _fence(session: Session, report: RestoreReport) -> list[tuple[str, int]]:
    """Revoke identities, invalidate leases, flag missing payloads. Returns inputs to remove."""
    now = clock.now()
    ended: list[tuple[str, int]] = []
    with broker._immediate(session):
        storage_ids = {org.id: org.storage_id for org in session.exec(select(Organization)).all()}

        for cred in session.exec(select(Credential).where(Credential.revoked_at.is_(None))).all():
            cred.revoked_at = now
            session.add(cred)
            report.credentials_revoked += 1
        for worker in session.exec(select(Worker).where(Worker.revoked_at.is_(None))).all():
            worker.revoked_at = now
            session.add(worker)
            report.workers_revoked += 1

        for job in session.exec(select(Job).where(Job.status == JobStatus.PROCESSING)).all():
            if job.cancel_requested:
                broker._finish_terminal(job, JobStatus.CANCELLED, "cancelled", now)
            else:
                broker._release(job, now, code="lease_expired", retryable=True)
            session.add(job)
            report.leases_invalidated += 1
            if job.status != JobStatus.PENDING:
                ended.append((storage_ids[job.organization_id], job.id))

        for job in session.exec(select(Job).where(Job.status == JobStatus.PENDING)).all():
            storage_id = storage_ids[job.organization_id]
            missing = not storage.job_input_path(storage_id, job.id).is_file() or (
                _has_postprocess(job) and not storage.postprocess_path(storage_id, job.id).is_file()
            )
            if missing:
                broker._finish_terminal(job, JobStatus.FAILED, CONTENT_UNAVAILABLE, now)
                job.error_message = "job input was not restored"
                session.add(job)
                report.inputs_unavailable += 1
                ended.append((storage_id, job.id))

        for job in session.exec(select(Job).where(Job.status == JobStatus.COMPLETED)).all():
            if job.error_code == CONTENT_UNAVAILABLE:
                continue
            if job.result_expires_at is not None and clock.as_utc(job.result_expires_at) <= now:
                continue  # retention already answers 410 result_expired
            if not storage.result_path(storage_ids[job.organization_id], job.id).is_file():
                job.error_code = CONTENT_UNAVAILABLE
                job.error_message = "job result was not restored"
                job.updated_at = now
                session.add(job)
                report.results_unavailable += 1

        unconsumed = select(Upload).where(Upload.consumed_by_job_id.is_(None))
        for upload in session.exec(unconsumed).all():
            if not storage.upload_path(storage_ids[upload.organization_id], upload.id).is_file():
                session.delete(upload)
                report.uploads_dropped += 1
    return ended


def restore(backup: Path) -> RestoreReport:
    """Restore ``backup`` over the configured hub database; see the module docstring."""
    backup = backup.resolve()
    lock = hublock.acquire()
    try:
        target = _target_database()
        if backup == target:
            raise RestoreError("backup is the live database")
        _check_backup(backup)
        _check_unlocked(target)
        _replace_database(backup, target)
        db_engine.create_db_and_tables()
        report = RestoreReport()
        with Session(db_engine.engine) as session:
            ended = _fence(session, report)
        for storage_id, job_id in ended:
            broker._remove_input(storage_id, job_id)
        report.maintenance = maintenance.run_once()
        return report
    finally:
        lock.release()


__all__ = ["RestoreError", "RestoreReport", "restore"]
