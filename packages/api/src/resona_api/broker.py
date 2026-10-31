"""Job broker for outbound pull workers: fenced claims, leases, results and failures.

Database and filesystem logic only, with no HTTP types. The worker routes map
:class:`BrokerError` onto status codes. Every state change runs in one short
``BEGIN IMMEDIATE`` transaction, so concurrent claims serialise on SQLite's
write lock; no transaction spans polling, transfer or inference.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from resona_job_core import (
    Candidate,
    EndpointKind,
    JobResult,
    PolicyDenied,
    Stage,
    check_policy,
)

from . import clock, storage
from .auth import hash_token
from .db.models import Job, JobStatus, Organization, Worker
from .settings_store import current_settings

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
LEASE_SECONDS = 120
RESULT_TTL = timedelta(hours=24)
MAX_MESSAGE_CHARS = 500


class BrokerError(Exception):
    """A rejected worker call; ``status`` and ``code`` are the HTTP status and error code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Claim:
    job_id: int
    kind: str
    lease_token: str
    lease_until: datetime
    attempt: int
    input_sha256: str | None
    execution_snapshot: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "lease_token": self.lease_token,
            "lease_until": self.lease_until.isoformat(),
            "attempt": self.attempt,
            "input_sha256": self.input_sha256,
            "execution_snapshot": self.execution_snapshot,
        }


@dataclass(frozen=True)
class ResultSlot:
    """Where an accepted worker may stage its result for one attempt."""

    storage_id: str
    attempt: int
    kind: str


def _now() -> datetime:
    return clock.now()


def _utc(value: datetime | None) -> datetime | None:
    """Database datetimes come back naive (UTC); treat them as UTC."""
    return None if value is None else clock.as_utc(value)


def _candidates(raw: str | None) -> list[Candidate]:
    return [Candidate.model_validate(item) for item in json.loads(raw or "[]")]


def _dump_candidates(items: list[Candidate]) -> str:
    return json.dumps([c.model_dump(mode="json") for c in items], sort_keys=True)


def _snapshot(job: Job) -> dict[str, Any]:
    return json.loads(job.execution_snapshot or "{}")


def _snapshot_candidates(job: Job) -> list[Candidate]:
    return [Candidate.model_validate(item) for item in _snapshot(job).get("candidates", [])]


@contextmanager
def _immediate(session: Session) -> Iterator[None]:
    """Run the block in one ``BEGIN IMMEDIATE`` transaction, committing on success."""
    session.commit()  # end any transaction the caller opened before
    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def _approved_worker(session: Session, *, worker_id: str, organization_id: str) -> Worker:
    worker = session.get(Worker, worker_id)
    if worker is None or worker.organization_id != organization_id or worker.revoked_at is not None:
        raise BrokerError(401, "invalid_credential", "worker credential is not active")
    if not worker.approved:
        raise BrokerError(403, "worker_not_approved", "worker is not approved")
    return worker


def _load_job(session: Session, *, organization_id: str, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.organization_id != organization_id:
        raise BrokerError(404, "job_not_found", "job not found")
    return job


def _lease_job(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    job_id: int,
    lease_token: str,
    allow_cancel: bool = False,
) -> Job:
    """Return the job when the presented lease is the current, unexpired lease of this worker."""
    now = _now()
    _approved_worker(session, worker_id=worker_id, organization_id=organization_id)
    job = _load_job(session, organization_id=organization_id, job_id=job_id)
    if (
        job.status != JobStatus.PROCESSING
        or job.worker_id != worker_id
        or job.lease_token_hash is None
        or job.lease_until is None
        or _utc(job.lease_until) <= now
        or not hmac.compare_digest(job.lease_token_hash, hash_token(lease_token))
    ):
        raise BrokerError(409, "stale_lease", "lease is not current")
    if job.cancel_requested and not allow_cancel:
        raise BrokerError(409, "stale_lease", "job was cancelled")
    return job


def _settings_or_none(session: Session, organization_id: str):
    try:
        _, settings = current_settings(session, organization_id)
    except LookupError:
        return None
    return settings


def _finish_terminal(job: Job, status: JobStatus, code: str, now: datetime) -> None:
    job.status = status
    job.error_code = code
    job.completed_at = now
    job.lease_token_hash = None
    job.lease_until = None
    job.input_deleted = True
    job.updated_at = now


def _release(job: Job, now: datetime, *, code: str, retryable: bool) -> None:
    """Requeue a released assignment, or fail the job when no attempt or time remains."""
    deadline_passed = _utc(job.deadline_at) <= now
    if retryable and not deadline_passed and job.attempt < job.max_attempts:
        job.status = JobStatus.PENDING
        job.worker_id = None
        job.candidate = None
        job.lease_token_hash = None
        job.lease_until = None
        job.updated_at = now
        return
    if deadline_passed:
        final = "deadline_exceeded"
    elif retryable:
        final = "attempts_exhausted"
    else:
        final = code
    _finish_terminal(job, JobStatus.FAILED, final, now)


def _remove_input(storage_id: str, job_id: int) -> None:
    """Remove the job's content inputs: the media and its resolved postprocess profile."""
    storage.remove_file(storage.job_input_path(storage_id, job_id))
    storage.remove_file(storage.postprocess_path(storage_id, job_id))


def _accepted_duplicate(job: Job, *, worker_id: str, lease_token: str, sha256: str) -> None:
    """A completed job answers only its own accepted lease; anything else is stale."""
    manifest = json.loads(job.result_manifest or "{}")
    same_lease = (
        manifest.get("lease_token_hash") == hash_token(lease_token)
        and manifest.get("attempt") == job.attempt
        and manifest.get("worker_id") == worker_id
    )
    if not same_lease:
        raise BrokerError(409, "stale_lease", "job already completed under another lease")
    if not hmac.compare_digest(str(manifest.get("sha256", "")), sha256):
        raise BrokerError(409, "result_conflict", "a different result was already accepted")


def record_heartbeat(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    runtime_version: str,
    reported: list[Candidate],
) -> dict[str, Any]:
    """Record readiness and version. Approval fields are never touched here."""
    now = _now()
    worker = session.get(Worker, worker_id)
    if worker is None or worker.organization_id != organization_id or worker.revoked_at is not None:
        raise BrokerError(401, "invalid_credential", "worker credential is not active")
    worker.runtime_version = runtime_version
    worker.reported_capabilities = _dump_candidates(reported)
    worker.last_seen = now
    session.add(worker)
    session.commit()
    return {
        "worker_id": worker.id,
        "approved": worker.approved,
        "approved_private": worker.approved_private,
        "approved_capabilities": [c.model_dump(mode="json") for c in _candidates(worker.approved_capabilities)],
        "server_time": now.isoformat(),
    }


def try_claim(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    capabilities: list[Candidate],
) -> Claim | None:
    """Claim the oldest eligible pending job, or return None when nothing is eligible.

    Non-private jobs whose organization no longer allows cloud processing are
    failed with ``policy_denied`` in the same transaction and skipped.
    """
    now = _now()
    requested = set(capabilities)
    claim: Claim | None = None
    denied: list[int] = []
    with _immediate(session):
        worker = _approved_worker(session, worker_id=worker_id, organization_id=organization_id)
        worker.last_seen = now
        session.add(worker)
        allowed = set(_candidates(worker.approved_capabilities)) & requested
        storage_id = session.get(Organization, organization_id).storage_id
        settings = _settings_or_none(session, organization_id)

        pending = session.exec(
            select(Job)
            .where(Job.organization_id == organization_id, Job.status == JobStatus.PENDING)
            .order_by(Job.id)
        ).all()
        chosen: tuple[int, int, Candidate] | None = None
        for job in pending:
            if not job.private and (settings is None or not settings.cloud_enabled):
                _finish_terminal(job, JobStatus.FAILED, "policy_denied", now)
                session.add(job)
                denied.append(job.id)
                continue
            if chosen is not None or not allowed:
                continue
            if job.cancel_requested or _utc(job.deadline_at) <= now or job.attempt >= job.max_attempts:
                continue
            if job.private and not worker.approved_private:
                continue
            pick = next((c for c in _snapshot_candidates(job) if c in allowed), None)
            if pick is not None:
                chosen = (job.id, job.attempt, pick)

        if chosen is not None:
            job_id, prior_attempt, pick = chosen
            token = secrets.token_urlsafe(32)
            until = now + timedelta(seconds=LEASE_SECONDS)
            result = session.exec(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.PENDING)
                .values(
                    status=JobStatus.PROCESSING,
                    attempt=prior_attempt + 1,
                    worker_id=worker.id,
                    candidate=pick.model_dump_json(),
                    lease_token_hash=hash_token(token),
                    lease_until=until,
                    updated_at=now,
                ),
                execution_options={"synchronize_session": False},
            )
            if result.rowcount == 1:
                session.expire_all()
                job = session.get(Job, job_id)
                snapshot = _snapshot(job)
                snapshot["candidate"] = pick.model_dump(mode="json")
                claim = Claim(
                    job_id=job_id,
                    kind=job.kind,
                    lease_token=token,
                    lease_until=until,
                    attempt=prior_attempt + 1,
                    input_sha256=job.input_sha256,
                    execution_snapshot=snapshot,
                )
    for job_id in denied:
        _remove_input(storage_id, job_id)
    return claim


def heartbeat_job(
    session: Session, *, worker_id: str, organization_id: str, job_id: int, lease_token: str
) -> dict[str, Any]:
    now = _now()
    until = now + timedelta(seconds=LEASE_SECONDS)
    with _immediate(session):
        job = _lease_job(
            session,
            worker_id=worker_id,
            organization_id=organization_id,
            job_id=job_id,
            lease_token=lease_token,
            allow_cancel=True,
        )
        job.lease_until = until
        job.updated_at = now
        session.add(job)
        cancel_requested = job.cancel_requested
    return {"lease_until": until.isoformat(), "cancel_requested": cancel_requested}


def input_path(
    session: Session, *, worker_id: str, organization_id: str, job_id: int, lease_token: str
) -> Path:
    job = _lease_job(
        session,
        worker_id=worker_id,
        organization_id=organization_id,
        job_id=job_id,
        lease_token=lease_token,
    )
    if job.input_deleted:
        raise BrokerError(410, "input_gone", "job input has been removed")
    storage_id = session.get(Organization, organization_id).storage_id
    path = storage.job_input_path(storage_id, job_id)
    if not path.is_file():
        raise BrokerError(410, "input_gone", "job input has been removed")
    return path


def postprocess_path(
    session: Session, *, worker_id: str, organization_id: str, job_id: int, lease_token: str
) -> Path:
    """The leased job's resolved postprocess profile; same checks as :func:`input_path`."""
    job = _lease_job(
        session,
        worker_id=worker_id,
        organization_id=organization_id,
        job_id=job_id,
        lease_token=lease_token,
    )
    if job.input_deleted:
        raise BrokerError(410, "input_gone", "job input has been removed")
    storage_id = session.get(Organization, organization_id).storage_id
    path = storage.postprocess_path(storage_id, job_id)
    if not path.is_file():
        raise BrokerError(404, "postprocess_not_found", "job has no postprocess profile")
    return path


def check_stage(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    job_id: int,
    lease_token: str,
    stage: str,
    endpoint_id: str | None,
) -> None:
    """Fresh policy check against current organization settings before an external stage."""
    job = _lease_job(
        session,
        worker_id=worker_id,
        organization_id=organization_id,
        job_id=job_id,
        lease_token=lease_token,
    )
    settings = _settings_or_none(session, organization_id)
    if settings is None:
        raise BrokerError(403, "policy_denied", "organization has no settings")
    endpoint = None
    if endpoint_id is not None:
        endpoint = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
        if endpoint is None:
            raise BrokerError(403, "policy_denied", "endpoint is not registered")
    try:
        check_policy(settings.policy(private=job.private), [Stage(name=stage, kind="llm", endpoint=endpoint)])
    except PolicyDenied as exc:
        raise BrokerError(403, "policy_denied", str(exc)) from exc


def begin_result(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    job_id: int,
    lease_token: str,
    sha256: str,
) -> ResultSlot | None:
    """Admit a result upload. Returns None when this exact result was already accepted."""
    job = _load_job(session, organization_id=organization_id, job_id=job_id)
    _approved_worker(session, worker_id=worker_id, organization_id=organization_id)
    if job.status == JobStatus.COMPLETED:
        _accepted_duplicate(job, worker_id=worker_id, lease_token=lease_token, sha256=sha256)
        return None
    job = _lease_job(
        session,
        worker_id=worker_id,
        organization_id=organization_id,
        job_id=job_id,
        lease_token=lease_token,
    )
    storage_id = session.get(Organization, organization_id).storage_id
    return ResultSlot(storage_id=storage_id, attempt=job.attempt, kind=job.kind)


def parse_result(path: Path, kind: str) -> None:
    """Validate a staged result file against the JobResult contract."""
    try:
        result = JobResult.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise BrokerError(422, "invalid_result", "result does not match the JobResult schema") from exc
    if result.kind != kind:
        raise BrokerError(422, "kind_mismatch", "result kind does not match the job")


def finish_result(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    job_id: int,
    lease_token: str,
    sha256: str,
    temp_path: Path,
    size: int,
) -> str:
    """Promote a validated staged result and complete the job in one locked transaction.

    The lease is re-checked under the write lock, so a superseded attempt can never
    replace the result of the attempt that won.
    """
    now = _now()
    with _immediate(session):
        job = _load_job(session, organization_id=organization_id, job_id=job_id)
        _approved_worker(session, worker_id=worker_id, organization_id=organization_id)
        if job.status == JobStatus.COMPLETED:
            _accepted_duplicate(job, worker_id=worker_id, lease_token=lease_token, sha256=sha256)
            storage.remove_file(temp_path)
            return "completed"
        job = _lease_job(
            session,
            worker_id=worker_id,
            organization_id=organization_id,
            job_id=job_id,
            lease_token=lease_token,
        )
        storage_id = session.get(Organization, organization_id).storage_id
        dest = storage.result_path(storage_id, job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, dest)
        manifest = {
            "sha256": sha256,
            "size": size,
            "attempt": job.attempt,
            "worker_id": worker_id,
            "lease_token_hash": job.lease_token_hash,
        }
        job.status = JobStatus.COMPLETED
        job.result_manifest = json.dumps(manifest, sort_keys=True)
        job.completed_at = now
        job.result_expires_at = now + RESULT_TTL
        job.lease_token_hash = None
        job.lease_until = None
        job.input_deleted = True
        job.updated_at = now
        session.add(job)
    _remove_input(storage_id, job_id)
    return "completed"


def fail_job(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    job_id: int,
    lease_token: str,
    code: str,
    message: str,
    retryable: bool,
    withdraw: Candidate | None,
) -> str:
    """Release or fail the current assignment.

    ``withdraw`` removes a capability this worker can no longer serve (for example
    after an out-of-memory failure) until it is re-prepared and re-approved.
    """
    now = _now()
    with _immediate(session):
        job = _lease_job(
            session,
            worker_id=worker_id,
            organization_id=organization_id,
            job_id=job_id,
            lease_token=lease_token,
            allow_cancel=True,
        )
        if withdraw is not None:
            worker = session.get(Worker, worker_id)
            remaining = [c for c in _candidates(worker.approved_capabilities) if c != withdraw]
            worker.approved_capabilities = _dump_candidates(remaining)
            session.add(worker)
        job.error_message = message[:MAX_MESSAGE_CHARS]
        if job.cancel_requested:
            _finish_terminal(job, JobStatus.CANCELLED, code, now)
        else:
            _release(job, now, code=code, retryable=retryable)
        session.add(job)
        status = job.status.value
    if status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        _remove_input(session.get(Organization, organization_id).storage_id, job_id)
    return status


def sweep_leases(session: Session) -> int:
    """Requeue or fail expired leases and pending jobs past their deadline. Returns rows changed."""
    now = _now()
    ended: list[tuple[str, int]] = []
    changed = 0
    with _immediate(session):
        storage_ids = {org.id: org.storage_id for org in session.exec(select(Organization)).all()}
        for job in session.exec(select(Job).where(Job.status == JobStatus.PROCESSING)).all():
            if job.lease_until is None or _utc(job.lease_until) > now:
                continue
            if job.cancel_requested:
                _finish_terminal(job, JobStatus.CANCELLED, "cancelled", now)
            else:
                _release(job, now, code="lease_expired", retryable=True)
            session.add(job)
            changed += 1
            if job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                ended.append((storage_ids[job.organization_id], job.id))
        for job in session.exec(select(Job).where(Job.status == JobStatus.PENDING)).all():
            if _utc(job.deadline_at) <= now:
                _finish_terminal(job, JobStatus.FAILED, "deadline_exceeded", now)
                session.add(job)
                changed += 1
                ended.append((storage_ids[job.organization_id], job.id))
    for storage_id, job_id in ended:
        _remove_input(storage_id, job_id)
    return changed


__all__ = [
    "BrokerError",
    "Claim",
    "LEASE_SECONDS",
    "PROTOCOL_VERSION",
    "ResultSlot",
    "begin_result",
    "check_stage",
    "fail_job",
    "finish_result",
    "heartbeat_job",
    "input_path",
    "parse_result",
    "postprocess_path",
    "record_heartbeat",
    "sweep_leases",
    "try_claim",
]
