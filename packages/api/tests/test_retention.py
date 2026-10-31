"""Hub maintenance: lease expiry, deadlines and content retention.

Time is injected through ``clock.now``; each pass is one ``maintenance.run_once``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlmodel import Session

from resona_job_core import Candidate
from resona_api import broker, clock, maintenance, storage
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import IdempotencyRecord, Job, JobStatus, Organization, Upload

CAND = Candidate(
    engine="faster-whisper",
    model="Systran/faster-whisper-large-v3",
    model_revision="rev-1",
    accelerator="cpu",
)
T0 = datetime(2031, 3, 1, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean_retention_tables():
    """Counts are global per pass, so start every test without leftover rows."""
    with Session(engine) as session:
        session.execute(text("DELETE FROM upload"))
        session.execute(text("DELETE FROM idempotencyrecord"))
        session.commit()


@pytest.fixture
def at(monkeypatch):
    def set_time(when: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time(T0)
    return set_time


def storage_id(org_id: str) -> str:
    with Session(engine) as session:
        return session.get(Organization, org_id).storage_id


def make_job(org_id: str, **fields) -> int:
    data = b"input"
    with Session(engine) as session:
        job = Job(
            organization_id=org_id,
            kind="audio",
            execution_snapshot=json.dumps({"candidates": [CAND.model_dump(mode="json")]}),
            input_sha256=hashlib.sha256(data).hexdigest(),
            deadline_at=T0 + timedelta(hours=24),
            **fields,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    storage.write_bytes_atomic(storage.job_input_path(storage_id(org_id), job_id), data)
    return job_id


def make_completed_job(org_id: str) -> int:
    job_id = make_job(
        org_id,
        status=JobStatus.COMPLETED,
        completed_at=T0,
        result_expires_at=T0 + timedelta(hours=24),
        result_manifest=json.dumps({"sha256": "0" * 64}),
        input_deleted=True,
    )
    sid = storage_id(org_id)
    storage.remove_file(storage.job_input_path(sid, job_id))
    storage.write_bytes_atomic(storage.result_path(sid, job_id), b'{"result": true}')
    return job_id


def make_upload(org_id: str, upload_id: str, *, consumed_by: int | None = None) -> None:
    with Session(engine) as session:
        session.add(Upload(
            id=upload_id, organization_id=org_id, sha256="0" * 64, size=5,
            media_family="audio", media_type="audio/wav",
            created_at=T0, expires_at=T0 + timedelta(hours=1), consumed_by_job_id=consumed_by,
        ))
        session.commit()
    storage.write_bytes_atomic(storage.upload_path(storage_id(org_id), upload_id), b"audio")


def job_row(job_id: int) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def test_unattached_upload_expires_after_one_hour(fresh_org, at):
    make_upload(fresh_org, "upl_unattached")
    make_upload(fresh_org, "upl_consumed", consumed_by=1)
    sid = storage_id(fresh_org)

    at(T0 + timedelta(minutes=59))
    assert maintenance.run_once()["uploads_expired"] == 0
    assert storage.upload_path(sid, "upl_unattached").exists()

    at(T0 + timedelta(hours=1))
    assert maintenance.run_once()["uploads_expired"] == 1
    assert not storage.upload_path(sid, "upl_unattached").exists()
    assert storage.upload_path(sid, "upl_consumed").exists()
    with Session(engine) as session:
        assert session.get(Upload, "upl_unattached") is None
        assert session.get(Upload, "upl_consumed") is not None


def test_result_file_expires_after_24_hours_and_row_stays(fresh_org, at):
    job_id = make_completed_job(fresh_org)
    result = storage.result_path(storage_id(fresh_org), job_id)

    at(T0 + timedelta(hours=23))
    assert maintenance.run_once()["results_expired"] == 0
    assert result.exists()

    at(T0 + timedelta(hours=24))
    assert maintenance.run_once()["results_expired"] == 1
    assert not result.exists()
    row = job_row(job_id)
    assert row.status == JobStatus.COMPLETED
    assert row.result_manifest is not None

    assert maintenance.run_once()["results_expired"] == 0


def test_metadata_job_dir_and_idempotency_record_deleted_after_seven_days(fresh_org, at):
    job_id = make_completed_job(fresh_org)
    directory = storage.job_dir(storage_id(fresh_org), job_id)
    with Session(engine) as session:
        session.add(IdempotencyRecord(
            organization_id=fresh_org, key="retry-key", request_hash="h", job_id=job_id, created_at=T0,
        ))
        session.commit()

    at(T0 + timedelta(days=7) - timedelta(seconds=1))
    assert maintenance.run_once()["metadata_deleted"] == 0
    assert job_row(job_id) is not None

    at(T0 + timedelta(days=7))
    counts = maintenance.run_once()
    assert counts["metadata_deleted"] == 2
    assert job_row(job_id) is None
    assert not directory.exists()
    with Session(engine) as session:
        assert session.get(IdempotencyRecord, (fresh_org, "retry-key")) is None


def test_expired_lease_is_requeued_and_abandoned_attempt_removed(fresh_org, at):
    job_id = make_job(fresh_org)
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "retention-worker", fresh_org)
        approve_worker(session, worker_id, private=True, capabilities=[CAND.model_dump(mode="json")])
    with Session(engine) as session:
        claim = broker.try_claim(
            session, worker_id=worker_id, organization_id=fresh_org, capabilities=[CAND]
        )
    assert claim.job_id == job_id
    attempt = storage.attempt_dir(storage_id(fresh_org), job_id, claim.attempt)
    attempt.mkdir(parents=True)
    (attempt / "result-partial.tmp").write_bytes(b"partial")

    at(T0 + timedelta(seconds=broker.LEASE_SECONDS - 1))
    assert maintenance.run_once()["leases_swept"] == 0
    assert attempt.exists()

    at(T0 + timedelta(seconds=broker.LEASE_SECONDS + 1))
    assert maintenance.run_once()["leases_swept"] == 1
    row = job_row(job_id)
    assert row.status == JobStatus.PENDING
    assert row.lease_token_hash is None
    assert not attempt.exists()
    assert storage.job_input_path(storage_id(fresh_org), job_id).exists()

    with Session(engine) as session:
        reclaim = broker.try_claim(
            session, worker_id=worker_id, organization_id=fresh_org, capabilities=[CAND]
        )
    assert (reclaim.job_id, reclaim.attempt) == (job_id, 2)
    with Session(engine) as session:
        path = broker.input_path(
            session, worker_id=worker_id, organization_id=fresh_org,
            job_id=job_id, lease_token=reclaim.lease_token,
        )
    assert path.read_bytes() == b"input"


def test_pending_job_past_deadline_fails_and_loses_input(fresh_org, at):
    job_id = make_job(fresh_org)
    input_file = storage.job_input_path(storage_id(fresh_org), job_id)

    at(T0 + timedelta(hours=24))
    assert maintenance.run_once()["leases_swept"] == 1
    row = job_row(job_id)
    assert (row.status, row.error_code) == (JobStatus.FAILED, "deadline_exceeded")
    assert not input_file.exists()


def test_only_stale_staging_files_are_swept(fresh_org, at):
    staged = storage.staging_path(storage.upload_path(storage_id(fresh_org), "upl_streaming"))
    os.utime(staged, (T0.timestamp(), T0.timestamp()))

    at(T0 + timedelta(minutes=30))
    maintenance.run_once()
    assert staged.exists()

    at(T0 + timedelta(hours=1))
    assert maintenance.run_once()["staging_swept"] >= 1
    assert not staged.exists()


def test_terminal_job_leftovers_are_reconciled(fresh_org, at):
    job_id = make_job(fresh_org, status=JobStatus.FAILED, completed_at=T0, error_code="x")
    sid = storage_id(fresh_org)
    storage.write_bytes_atomic(storage.result_path(sid, job_id), b"{}")

    maintenance.run_once()

    assert not storage.job_input_path(sid, job_id).exists()
    assert not storage.result_path(sid, job_id).exists()
    assert job_row(job_id) is not None
