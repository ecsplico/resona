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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from resona_job_core import (
    Candidate,
    Endpoint,
    JobResult,
    OrganizationSettings,
    PolicyDenied,
    Stage,
    check_policy,
)

from . import clock, releases, storage
from .auth import hash_token
from .engine_registry import cloud_asr_endpoint_id
from .db.models import Job, JobStatus, Organization, Worker
from .settings_store import current_settings

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
LEASE_SECONDS = 120
RESULT_TTL = timedelta(hours=24)
MAX_MESSAGE_CHARS = 500
JOB_DELETED = "deleted"
"""Error code of a deleted job's tombstone: kept (cancelled, lease fenced, invisible
to clients) only until the lease it replaced would have expired, then purged."""


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
class LeaseRef:
    """What a lease-bound worker call presents: who, which job, and the lease token."""

    worker_id: str
    organization_id: str
    job_id: int
    lease_token: str


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


def _lease_job(session: Session, lease: LeaseRef, *, allow_cancel: bool = False) -> Job:
    """Return the job when the presented lease is the current, unexpired lease of this worker."""
    now = _now()
    _approved_worker(session, worker_id=lease.worker_id, organization_id=lease.organization_id)
    job = _load_job(session, organization_id=lease.organization_id, job_id=lease.job_id)
    if (
        job.status != JobStatus.PROCESSING
        or job.worker_id != lease.worker_id
        or job.lease_token_hash is None
        or job.lease_until is None
        or _utc(job.lease_until) <= now
        or not hmac.compare_digest(job.lease_token_hash, hash_token(lease.lease_token))
    ):
        raise BrokerError(409, "stale_lease", "lease is not current")
    if job.cancel_requested and not allow_cancel:
        raise BrokerError(409, "stale_lease", "job was cancelled")
    return job


def _settings_or_none(session: Session, organization_id: str) -> OrganizationSettings | None:
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


def purge_job(session: Session, job: Job) -> None:
    """Delete ``job`` in the caller's write transaction; the caller removes its directory after commit.

    A pending or terminal job's row goes at once. A processing job is cancelled
    first: its lease is fenced, its row kept as an invisible ``deleted`` tombstone
    until the replaced lease would have expired (so a late worker cannot resurrect
    it), and :func:`sweep_leases` purges it afterwards.
    """
    if job.status == JobStatus.PROCESSING:
        lease_until = job.lease_until
        _finish_terminal(job, JobStatus.CANCELLED, JOB_DELETED, _now())
        job.cancel_requested = True
        job.error_message = "deleted by client"
        job.lease_until = lease_until
        session.add(job)
    else:
        session.delete(job)


def _accepted_duplicate(job: Job, lease: LeaseRef, sha256: str) -> None:
    """A completed job answers only its own accepted lease; anything else is stale."""
    manifest = json.loads(job.result_manifest or "{}")
    same_lease = (
        manifest.get("lease_token_hash") == hash_token(lease.lease_token)
        and manifest.get("attempt") == job.attempt
        and manifest.get("worker_id") == lease.worker_id
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
    text_jobs: bool = False,
    diarize: bool = False,
) -> dict[str, Any]:
    """Record readiness, version and whether the worker runs text jobs and diarizes. Approval fields are never touched here."""
    now = _now()
    worker = session.get(Worker, worker_id)
    if worker is None or worker.organization_id != organization_id or worker.revoked_at is not None:
        raise BrokerError(401, "invalid_credential", "worker credential is not active")
    worker.runtime_version = runtime_version
    worker.reported_capabilities = _dump_candidates(reported)
    worker.text_jobs = text_jobs
    worker.diarize = diarize
    worker.last_seen = now
    session.add(worker)
    session.commit()
    return {
        "worker_id": worker.id,
        "approved": worker.approved,
        "approved_private": worker.approved_private,
        "approved_capabilities": [c.model_dump(mode="json") for c in _candidates(worker.approved_capabilities)],
        "server_time": now.isoformat(),
        # Tells the worker this hub accepts `diarize` in claim bodies; hubs predating it omit the key.
        "diarize": True,
    }


def job_endpoint_ids(snapshot: dict[str, Any], kind: str, candidate: Candidate | None) -> list[str]:
    """Endpoints a job uses when run with ``candidate``: its postprocess LLM endpoints
    plus the cloud ASR endpoint the candidate implies (text jobs have no candidate).

    Snapshots admitted before ``endpoint_ids`` was recorded count every endpoint of
    their policy registry when they carry a postprocess profile.
    """
    ids = snapshot.get("endpoint_ids")
    if ids is None:
        policy = snapshot.get("policy") or {}
        ids = [ep["id"] for ep in policy.get("endpoints", [])] if snapshot.get("postprocess") else []
    ids = list(ids)
    cloud = cloud_asr_endpoint_id(candidate.engine) if kind == "audio" and candidate is not None else None
    if cloud is not None:
        ids.append(cloud)
    return ids


def endpoints_assigned(
    snapshot: dict[str, Any],
    kind: str,
    candidate: Candidate | None,
    *,
    worker_id: str,
    settings: OrganizationSettings | None,
) -> bool:
    """Whether every endpoint the job uses that is assigned to a worker is assigned to ``worker_id``.

    Assignments come from the organization's current settings, falling back to the
    job's snapshot for endpoints no longer registered (those stages are denied by
    :func:`check_stage` anyway).
    """
    registry = {
        ep["id"]: Endpoint.model_validate(ep) for ep in (snapshot.get("policy") or {}).get("endpoints", [])
    }
    if settings is not None:
        registry.update((ep.id, ep) for ep in settings.endpoints)
    for endpoint_id in job_endpoint_ids(snapshot, kind, candidate):
        endpoint = registry.get(endpoint_id)
        if endpoint is not None and endpoint.worker_id not in (None, worker_id):
            return False
    return True


def cloud_denied(job: Job, settings: OrganizationSettings | None) -> bool:
    """A non-private job whose organization no longer allows cloud processing."""
    return not job.private and (settings is None or not settings.cloud_enabled)


def wants_diarize(snapshot: dict[str, Any], kind: str) -> bool:
    """Whether an audio job asked for speaker diarization (``AudioOptions.diarize``)."""
    return kind == "audio" and bool((snapshot.get("options") or {}).get("diarize"))


def try_claim(
    session: Session,
    *,
    worker_id: str,
    organization_id: str,
    capabilities: list[Candidate],
    text_jobs: bool = False,
    diarize: bool = False,
) -> Claim | None:
    """Claim the oldest eligible pending job, or return None when nothing is eligible.

    Eligible: privacy class equal to the worker's approval (private jobs only on
    private-approved workers, cloud opt-in jobs only on non-private ones), a
    snapshot candidate both offered and hub-approved (for a text job: the worker
    asked for text jobs; for a diarize job: the worker claims with ``diarize``),
    a worker release approved for it, and every worker-assigned endpoint of the
    job assigned to this worker.
    Ineligible jobs are skipped, never modified: non-private jobs whose
    organization disabled cloud processing are failed by :func:`sweep_leases`.
    """
    now = _now()
    requested = set(capabilities)
    claim: Claim | None = None
    with _immediate(session):
        worker = _approved_worker(session, worker_id=worker_id, organization_id=organization_id)
        worker.last_seen = now
        worker.diarize = diarize
        session.add(worker)
        allowed = set(_candidates(worker.approved_capabilities)) & requested
        if not allowed and not text_jobs:
            return None
        settings = _settings_or_none(session, organization_id)
        approved_releases = releases.approved_tuples(session, organization_id)
        release = worker.runtime_version
        known_releases = {release} if release and releases.is_known(session, release) else set()

        pending = session.exec(
            select(Job)
            .where(
                Job.organization_id == organization_id,
                Job.status == JobStatus.PENDING,
                Job.private == worker.approved_private,
            )
            .order_by(Job.id)
        ).all()
        chosen: tuple[int, int, Candidate | None] | None = None
        for job in pending:
            if job.cancel_requested or _utc(job.deadline_at) <= now or job.attempt >= job.max_attempts:
                continue
            if cloud_denied(job, settings):
                continue
            if job.kind == "text" and not text_jobs:
                continue
            snapshot = _snapshot(job)
            if wants_diarize(snapshot, job.kind) and not diarize:
                continue
            offered: list[Candidate | None] = (
                [None] if job.kind == "text" else [c for c in _snapshot_candidates(job) if c in allowed]
            )
            eligible = [
                c
                for c in offered
                if releases.may_run(
                    worker_release=worker.runtime_version,
                    purpose=job.purpose,
                    snapshot_release=snapshot.get("release_id"),
                    execution_profile=job.execution_profile,
                    candidate=c,
                    approved=approved_releases,
                    known=known_releases,
                )
                and endpoints_assigned(snapshot, job.kind, c, worker_id=worker.id, settings=settings)
            ]
            if eligible:
                chosen = (job.id, job.attempt, eligible[0])
                break

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
                    candidate=pick.model_dump_json() if pick is not None else None,
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
                snapshot["candidate"] = pick.model_dump(mode="json") if pick is not None else None
                claim = Claim(
                    job_id=job_id,
                    kind=job.kind,
                    lease_token=token,
                    lease_until=until,
                    attempt=prior_attempt + 1,
                    input_sha256=job.input_sha256,
                    execution_snapshot=snapshot,
                )
    return claim


def heartbeat_job(session: Session, lease: LeaseRef) -> dict[str, Any]:
    now = _now()
    until = now + timedelta(seconds=LEASE_SECONDS)
    with _immediate(session):
        job = _lease_job(session, lease, allow_cancel=True)
        job.lease_until = until
        job.updated_at = now
        session.add(job)
        cancel_requested = job.cancel_requested
    return {"lease_until": until.isoformat(), "cancel_requested": cancel_requested}


def input_path(session: Session, lease: LeaseRef) -> Path:
    job = _lease_job(session, lease)
    if job.input_deleted:
        raise BrokerError(410, "input_gone", "job input has been removed")
    storage_id = session.get(Organization, lease.organization_id).storage_id
    path = storage.job_input_path(storage_id, lease.job_id)
    if not path.is_file():
        raise BrokerError(410, "input_gone", "job input has been removed")
    return path


def postprocess_path(session: Session, lease: LeaseRef) -> Path:
    """The leased job's resolved postprocess profile; same checks as :func:`input_path`."""
    job = _lease_job(session, lease)
    if job.input_deleted:
        raise BrokerError(410, "input_gone", "job input has been removed")
    storage_id = session.get(Organization, lease.organization_id).storage_id
    path = storage.postprocess_path(storage_id, lease.job_id)
    if not path.is_file():
        raise BrokerError(404, "postprocess_not_found", "job has no postprocess profile")
    return path


def check_stage(session: Session, lease: LeaseRef, *, stage: str, endpoint_id: str | None) -> None:
    """Fresh policy check against current organization settings before an external stage.

    An endpoint assigned to another worker is denied: its secret file lives there.
    """
    job = _lease_job(session, lease)
    settings = _settings_or_none(session, lease.organization_id)
    if settings is None:
        raise BrokerError(403, "policy_denied", "organization has no settings")
    endpoint = None
    if endpoint_id is not None:
        endpoint = next((ep for ep in settings.endpoints if ep.id == endpoint_id), None)
        if endpoint is None:
            raise BrokerError(403, "policy_denied", "endpoint is not registered")
        if endpoint.worker_id is not None and endpoint.worker_id != lease.worker_id:
            raise BrokerError(403, "policy_denied", "endpoint is assigned to another worker")
    try:
        check_policy(settings.policy(private=job.private), [Stage(name=stage, kind="llm", endpoint=endpoint)])
    except PolicyDenied as exc:
        raise BrokerError(403, "policy_denied", str(exc)) from exc


def begin_result(session: Session, lease: LeaseRef, *, sha256: str) -> ResultSlot | None:
    """Admit a result upload. Returns None when this exact result was already accepted."""
    job = _load_job(session, organization_id=lease.organization_id, job_id=lease.job_id)
    _approved_worker(session, worker_id=lease.worker_id, organization_id=lease.organization_id)
    if job.status == JobStatus.COMPLETED:
        _accepted_duplicate(job, lease, sha256)
        return None
    job = _lease_job(session, lease)
    storage_id = session.get(Organization, lease.organization_id).storage_id
    return ResultSlot(storage_id=storage_id, attempt=job.attempt, kind=job.kind)


def parse_result(path: Path, kind: str) -> None:
    """Validate a staged result file against the JobResult contract."""
    try:
        result = JobResult.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        raise BrokerError(422, "invalid_result", "result does not match the JobResult schema") from exc
    if result.kind != kind:
        raise BrokerError(422, "kind_mismatch", "result kind does not match the job")


def finish_result(session: Session, lease: LeaseRef, *, sha256: str, temp_path: Path, size: int) -> str:
    """Promote a validated staged result and complete the job in one locked transaction.

    The lease is re-checked under the write lock, so a superseded attempt can never
    replace the result of the attempt that won.
    """
    now = _now()
    with _immediate(session):
        job = _load_job(session, organization_id=lease.organization_id, job_id=lease.job_id)
        _approved_worker(session, worker_id=lease.worker_id, organization_id=lease.organization_id)
        if job.status == JobStatus.COMPLETED:
            _accepted_duplicate(job, lease, sha256)
            storage.remove_file(temp_path)
            return "completed"
        job = _lease_job(session, lease)
        storage_id = session.get(Organization, lease.organization_id).storage_id
        dest = storage.result_path(storage_id, lease.job_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, dest)
        manifest = {
            "sha256": sha256,
            "size": size,
            "attempt": job.attempt,
            "worker_id": lease.worker_id,
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
    _remove_input(storage_id, lease.job_id)
    return "completed"


def _capability_label(candidate: Candidate) -> str:
    return f"{candidate.engine}:{candidate.model}@{candidate.accelerator}"


def fail_job(
    session: Session,
    lease: LeaseRef,
    *,
    code: str,
    message: str,
    retryable: bool,
    withdraw: Candidate | None,
) -> str:
    """Release or fail the current assignment.

    ``withdraw`` names a capability the worker stopped offering (for example after
    an out-of-memory failure). Withdrawal is worker-local: the worker stops
    reporting and claiming it until it is re-prepared. The hub only notes it on the
    job's error message; the worker's hub approval is never changed here.
    """
    now = _now()
    if withdraw is not None:
        message = f"{message} (worker withdrew {_capability_label(withdraw)})".strip()
    with _immediate(session):
        job = _lease_job(session, lease, allow_cancel=True)
        job.error_message = message[:MAX_MESSAGE_CHARS]
        if job.cancel_requested:
            _finish_terminal(job, JobStatus.CANCELLED, code, now)
        else:
            _release(job, now, code=code, retryable=retryable)
        session.add(job)
        status = job.status.value
    if status in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        _remove_input(session.get(Organization, lease.organization_id).storage_id, lease.job_id)
    return status


def sweep_leases(session: Session) -> int:
    """Requeue or fail expired leases, fail pending jobs past their deadline or no longer
    allowed by policy, and purge deleted-job tombstones whose lease is dead. Returns rows changed."""
    now = _now()
    ended: list[tuple[str, int]] = []
    changed = 0
    with _immediate(session):
        storage_ids = {org.id: org.storage_id for org in session.exec(select(Organization)).all()}
        settings: dict[str, OrganizationSettings | None] = {}
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
            elif not job.private:
                if job.organization_id not in settings:
                    settings[job.organization_id] = _settings_or_none(session, job.organization_id)
                if not cloud_denied(job, settings[job.organization_id]):
                    continue
                _finish_terminal(job, JobStatus.FAILED, "policy_denied", now)
                job.error_message = "cloud processing is disabled for this organization"
            else:
                continue
            session.add(job)
            changed += 1
            ended.append((storage_ids[job.organization_id], job.id))
        tombstones = select(Job).where(Job.status == JobStatus.CANCELLED, Job.error_code == JOB_DELETED)
        for job in session.exec(tombstones).all():
            if job.lease_until is None or _utc(job.lease_until) <= now:
                ended.append((storage_ids[job.organization_id], job.id))
                session.delete(job)
                changed += 1
    for storage_id, job_id in ended:
        _remove_input(storage_id, job_id)
    return changed


__all__ = [
    "BrokerError",
    "Claim",
    "JOB_DELETED",
    "LEASE_SECONDS",
    "LeaseRef",
    "PROTOCOL_VERSION",
    "ResultSlot",
    "begin_result",
    "check_stage",
    "cloud_denied",
    "endpoints_assigned",
    "job_endpoint_ids",
    "fail_job",
    "finish_result",
    "heartbeat_job",
    "input_path",
    "parse_result",
    "postprocess_path",
    "purge_job",
    "record_heartbeat",
    "sweep_leases",
    "try_claim",
]
