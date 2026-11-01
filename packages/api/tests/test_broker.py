"""Behavioural tests for the lease broker (claim, expiry sweep, deadlines).

These call :mod:`resona_api.broker` directly, without HTTP. Time is injected
through ``clock.now`` so lease expiry and deadlines are deterministic.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from resona_job_core import Candidate, Endpoint
from resona_api import broker, clock, storage
from resona_api.admin import approve_worker, enroll_worker
from resona_api.releases import hub_release_id
from release_approval import approve_release
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, Organization

CAND = Candidate(
    engine="mlx-whisper",
    model="mlx-community/whisper-large-v3-mlx",
    model_revision="rev-1",
    accelerator="metal",
)
T0 = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def frozen(monkeypatch):
    def set_time(when: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time(T0)
    return set_time


def make_worker(org_id: str, *, private: bool = True, capabilities=(CAND,)) -> str:
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "broker-worker", org_id)
        approve_worker(
            session, worker_id, private=private,
            capabilities=[c.model_dump(mode="json") for c in capabilities],
        ).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return worker_id


def make_job(org_id: str, *, private: bool = True, max_attempts: int = 3,
             data: bytes = b"input-bytes", snapshot: dict | None = None) -> int:
    with Session(engine) as session:
        job = Job(
            organization_id=org_id,
            kind="audio",
            private=private,
            execution_snapshot=json.dumps(
                {"candidates": [CAND.model_dump(mode="json")], **(snapshot or {})}
            ),
            input_sha256=hashlib.sha256(data).hexdigest(),
            deadline_at=clock.now() + timedelta(hours=24),
            max_attempts=max_attempts,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        storage_id = session.get(Organization, org_id).storage_id
        job_id = job.id
    storage.write_bytes_atomic(storage.job_input_path(storage_id, job_id), data)
    return job_id


def claim_as(worker_id: str, org_id: str):
    with Session(engine) as session:
        return broker.try_claim(
            session, worker_id=worker_id, organization_id=org_id, capabilities=[CAND]
        )


def job_row(job_id: int) -> Job:
    with Session(engine) as session:
        return session.get(Job, job_id)


def test_concurrent_claims_yield_exactly_one_lease(fresh_org):
    job_id = make_job(fresh_org)
    workers = [make_worker(fresh_org) for _ in range(10)]
    barrier = threading.Barrier(len(workers))
    results: list = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt(worker_id: str) -> None:
        try:
            barrier.wait()
            claim = claim_as(worker_id, fresh_org)
            with lock:
                results.append(claim)
        except BaseException as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(w,)) for w in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    won = [claim for claim in results if claim is not None]
    assert len(won) == 1
    assert won[0].job_id == job_id
    assert won[0].attempt == 1
    row = job_row(job_id)
    assert row.status == JobStatus.PROCESSING
    assert row.attempt == 1


def test_claim_returns_none_for_capabilities_not_offered(fresh_org):
    make_job(fresh_org)
    cpu = Candidate(engine="faster-whisper", model="large-v3", accelerator="cpu")
    worker_id = make_worker(fresh_org, capabilities=(cpu,))
    assert claim_as(worker_id, fresh_org) is None


def test_expired_lease_is_requeued_and_next_claim_increments_attempt(fresh_org, frozen):
    job_id = make_job(fresh_org)
    first = make_worker(fresh_org)
    second = make_worker(fresh_org)

    assert claim_as(first, fresh_org).attempt == 1

    frozen(T0 + timedelta(seconds=broker.LEASE_SECONDS + 1))
    with Session(engine) as session:
        assert broker.sweep_leases(session) == 1
    row = job_row(job_id)
    assert row.status == JobStatus.PENDING
    assert row.worker_id is None
    assert row.lease_token_hash is None

    reclaimed = claim_as(second, fresh_org)
    assert reclaimed.job_id == job_id
    assert reclaimed.attempt == 2


def test_lease_expiry_fails_job_once_attempts_are_spent(fresh_org, frozen):
    job_id = make_job(fresh_org, max_attempts=1)
    worker_id = make_worker(fresh_org)
    assert claim_as(worker_id, fresh_org).attempt == 1

    frozen(T0 + timedelta(seconds=broker.LEASE_SECONDS + 1))
    with Session(engine) as session:
        broker.sweep_leases(session)
    row = job_row(job_id)
    assert row.status == JobStatus.FAILED
    assert row.error_code == "attempts_exhausted"


def test_unexpired_lease_is_not_swept(fresh_org, frozen):
    job_id = make_job(fresh_org)
    worker_id = make_worker(fresh_org)
    claim_as(worker_id, fresh_org)

    frozen(T0 + timedelta(seconds=broker.LEASE_SECONDS - 1))
    with Session(engine) as session:
        assert broker.sweep_leases(session) == 0
    assert job_row(job_id).status == JobStatus.PROCESSING


def test_pending_job_past_deadline_fails_and_loses_its_input(fresh_org, frozen):
    job_id = make_job(fresh_org)
    with Session(engine) as session:
        org = session.get(Organization, fresh_org)
        storage_id = org.storage_id
    input_file = storage.job_input_path(storage_id, job_id)
    assert input_file.exists()

    frozen(T0 + timedelta(hours=25))
    with Session(engine) as session:
        assert broker.sweep_leases(session) == 1

    row = job_row(job_id)
    assert row.status == JobStatus.FAILED
    assert row.error_code == "deadline_exceeded"
    assert row.input_deleted is True
    assert not input_file.exists()


def test_cancel_requested_job_is_never_claimed(fresh_org):
    job_id = make_job(fresh_org)
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.cancel_requested = True
        session.add(job)
        session.commit()
    worker_id = make_worker(fresh_org)
    assert claim_as(worker_id, fresh_org) is None
    assert job_row(job_id).status == JobStatus.PENDING


def test_private_job_is_not_claimed_by_public_worker(fresh_org):
    job_id = make_job(fresh_org, private=True)
    public_worker = make_worker(fresh_org, private=False)
    assert claim_as(public_worker, fresh_org) is None
    assert job_row(job_id).status == JobStatus.PENDING


def test_non_private_job_is_not_claimed_by_private_worker(fresh_org, store_settings):
    store_settings(fresh_org, cloud_enabled=True)
    job_id = make_job(fresh_org, private=False)
    private_worker = make_worker(fresh_org, private=True)
    assert claim_as(private_worker, fresh_org) is None
    assert job_row(job_id).status == JobStatus.PENDING

    public_worker = make_worker(fresh_org, private=False)
    assert claim_as(public_worker, fresh_org).job_id == job_id


def test_ineligible_poll_leaves_cloud_denied_job_to_the_sweep(fresh_org):
    # Cloud is off by default: a non-private job can no longer run anywhere.
    job_id = make_job(fresh_org, private=False)
    before = job_row(job_id)
    private_worker = make_worker(fresh_org, private=True)
    public_worker = make_worker(fresh_org, private=False)

    assert claim_as(private_worker, fresh_org) is None
    assert claim_as(public_worker, fresh_org) is None
    after = job_row(job_id)
    assert (after.status, after.error_code, after.updated_at) == (JobStatus.PENDING, None, before.updated_at)

    with Session(engine) as session:
        broker.sweep_leases(session)
    swept = job_row(job_id)
    assert (swept.status, swept.error_code) == (JobStatus.FAILED, "policy_denied")
    with Session(engine) as session:
        storage_id = session.get(Organization, fresh_org).storage_id
    assert not storage.job_input_path(storage_id, job_id).exists()


def _local_llm(worker_id: str) -> Endpoint:
    return Endpoint(id="polish", kind="local_llm", model="qwen", url="http://127.0.0.1:8000/v1",
                    worker_id=worker_id)


def test_job_with_assigned_endpoint_is_claimed_only_by_that_worker(fresh_org, store_settings):
    worker_a = make_worker(fresh_org)
    worker_b = make_worker(fresh_org)
    endpoint = _local_llm(worker_a)
    store_settings(fresh_org, endpoints=[endpoint])
    job_id = make_job(fresh_org, snapshot={
        "endpoint_ids": ["polish"],
        "policy": {"endpoints": [endpoint.model_dump(mode="json")]},
    })

    assert claim_as(worker_b, fresh_org) is None
    assert job_row(job_id).status == JobStatus.PENDING
    assert claim_as(worker_a, fresh_org).job_id == job_id


def test_endpoint_reassigned_after_claim_is_denied_at_stage_check(fresh_org, store_settings):
    worker_a = make_worker(fresh_org)
    worker_b = make_worker(fresh_org)
    store_settings(fresh_org, endpoints=[_local_llm(worker_a)])
    job_id = make_job(fresh_org, snapshot={"endpoint_ids": ["polish"]})
    lease = claim_as(worker_a, fresh_org)
    ref = broker.LeaseRef(worker_id=worker_a, organization_id=fresh_org, job_id=job_id,
                          lease_token=lease.lease_token)
    with Session(engine) as session:
        broker.check_stage(session, ref, stage="polish", endpoint_id="polish")

    store_settings(fresh_org, endpoints=[_local_llm(worker_b)])
    with Session(engine) as session, pytest.raises(broker.BrokerError) as denied:
        broker.check_stage(session, ref, stage="polish", endpoint_id="polish")
    assert (denied.value.status, denied.value.code) == (403, "policy_denied")


def test_claimed_job_carries_chosen_candidate_in_snapshot(fresh_org, frozen):
    make_job(fresh_org)
    worker_id = make_worker(fresh_org)
    claim = claim_as(worker_id, fresh_org)
    assert claim.execution_snapshot["candidates"] == [CAND.model_dump(mode="json")]
    assert claim.lease_until.replace(tzinfo=UTC) == T0 + timedelta(seconds=broker.LEASE_SECONDS)
