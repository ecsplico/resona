"""Behavioural tests for the outbound pull-worker HTTP routes.

Covers approval and revocation, capability matching, private-job admission,
long-poll claims, lease fencing, duplicate and conflicting results,
cancellation, retry exhaustion and per-call policy checks. Time is injected
through ``clock.now`` so lease expiry is deterministic.

"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Candidate, Endpoint
from resona_api import admin, broker, clock, storage, worker_routes
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, Organization

CAND = Candidate(
    engine="mlx-whisper",
    model="mlx-community/whisper-large-v3-mlx",
    model_revision="rev-1",
    accelerator="metal",
)
CPU = Candidate(engine="faster-whisper", model="large-v3", accelerator="cpu")
T0 = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(worker_routes.router)
    return TestClient(app)


@pytest.fixture
def frozen(monkeypatch):
    """Pin ``clock.now`` to ``T0``; returns a setter for later instants."""

    def set_time(when: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time(T0)
    return set_time


def make_job(org_id, *, private=True, candidates=(CAND,), data=b"audio-bytes",
             max_attempts=3, cancel_requested=False, kind="audio") -> int:
    with Session(engine) as session:
        job = Job(
            organization_id=org_id,
            kind=kind,
            private=private,
            execution_snapshot=json.dumps(
                {"candidates": [c.model_dump(mode="json") for c in candidates]}
            ),
            input_sha256=hashlib.sha256(data).hexdigest(),
            deadline_at=clock.now() + timedelta(hours=24),
            max_attempts=max_attempts,
            cancel_requested=cancel_requested,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        storage_id = session.get(Organization, org_id).storage_id
        job_id = job.id
    storage.write_bytes_atomic(storage.job_input_path(storage_id, job_id), data)
    return job_id


def make_worker(org_id, *, approved=True, private=True, capabilities=(CAND,)):
    """Enroll a worker; approve it unless asked not to. Returns (worker_id, headers)."""
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "route-worker", org_id)
        if approved:
            approve_worker(
                session, worker_id, private=private,
                capabilities=[c.model_dump(mode="json") for c in capabilities],
            )
    return worker_id, {"Authorization": f"Bearer {token}"}


def job_row(job_id: int) -> Job:
    with Session(engine) as session:
        return session.get(Job, job_id)


def set_job(job_id: int, **changes) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        for key, value in changes.items():
            setattr(job, key, value)
        session.add(job)
        session.commit()


def heartbeat(http, headers, *, protocol=1, caps=(CAND,), extra=None):
    body = {
        "worker_protocol": protocol,
        "runtime_version": "0.1.0",
        "reported_capabilities": [c.model_dump(mode="json") for c in caps],
        **(extra or {}),
    }
    return http.post("/v1/workers/heartbeat", json=body, headers=headers)


def claim(http, headers, *, caps=(CAND,), wait=0):
    return http.post(
        f"/v1/workers/claim?wait={wait}",
        json={"capabilities": [c.model_dump(mode="json") for c in caps]},
        headers=headers,
    )


def lease_headers(headers, token):
    return {**headers, "X-Resona-Lease": token}


def result_body(kind="audio", markdown="# Hallo") -> bytes:
    return json.dumps({
        "schema_version": 1,
        "kind": kind,
        "raw_text": "Hallo Welt",
        "markdown": markdown,
        "provenance": {"stages": []},
    }).encode()


def post_result(http, job_id, headers, token, body: bytes, sha: str | None = None):
    return http.post(
        f"/v1/workers/jobs/{job_id}/result",
        content=body,
        headers={
            **lease_headers(headers, token),
            "X-Resona-Result-Sha256": sha or hashlib.sha256(body).hexdigest(),
            "Content-Type": "application/json",
        },
    )


def error_code(response) -> str:
    """Error bodies arrive as ``{"error": ...}`` or wrapped in FastAPI's ``detail``."""
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def test_unapproved_worker_heartbeats_but_cannot_claim(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org, approved=False)

    beat = heartbeat(http, headers, extra={"approved": True, "approved_private": True})
    assert beat.status_code == 200
    assert beat.json()["approved"] is False

    denied = claim(http, headers)
    assert denied.status_code == 403
    assert error_code(denied) == "worker_not_approved"
    assert job_row(job_id).status == JobStatus.PENDING


def test_revoked_worker_is_rejected_and_loses_input_download(http, fresh_org):
    make_job(fresh_org)
    worker_id, headers = make_worker(fresh_org)
    lease = claim(http, headers).json()

    with Session(engine) as session:
        admin.revoke_worker(session, worker_id)

    download = http.get(f"/v1/workers/jobs/{lease['job_id']}/input",
                        headers=lease_headers(headers, lease["lease_token"]))
    assert download.status_code == 401
    assert heartbeat(http, headers).status_code == 401


def test_self_declared_private_worker_never_claims_private_job(http, fresh_org):
    job_id = make_job(fresh_org, private=True)
    worker_id, headers = make_worker(fresh_org, private=False)

    beat = heartbeat(http, headers, extra={"approved_private": True})
    assert beat.json()["approved_private"] is False
    assert claim(http, headers).status_code == 204
    assert job_row(job_id).status == JobStatus.PENDING

    with Session(engine) as session:
        approve_worker(session, worker_id, private=True,
                       capabilities=[CAND.model_dump(mode="json")])
    claimed = claim(http, headers)
    assert claimed.status_code == 200
    assert claimed.json()["job_id"] == job_id


def test_capability_mismatch_returns_no_content_and_exact_match_claims(http, fresh_org):
    make_job(fresh_org, candidates=(CAND,))
    _, cpu_only = make_worker(fresh_org, capabilities=(CPU,))
    assert claim(http, cpu_only, caps=(CPU,)).status_code == 204

    # Same engine and model but a different revision is not the approved tuple.
    drifted = Candidate(engine=CAND.engine, model=CAND.model, model_revision="rev-2",
                        accelerator="metal")
    _, exact = make_worker(fresh_org, capabilities=(CAND,))
    assert claim(http, exact, caps=(drifted,)).status_code == 204

    claimed = claim(http, exact, caps=(CAND,))
    assert claimed.status_code == 200
    assert claimed.json()["attempt"] == 1
    assert claimed.json()["execution_snapshot"]["candidate"] == CAND.model_dump(mode="json")


def test_long_poll_returns_204_after_wait(http, fresh_org):
    _, headers = make_worker(fresh_org)
    started = time.monotonic()
    response = claim(http, headers, wait=1)
    elapsed = time.monotonic() - started
    assert response.status_code == 204
    assert 0.9 <= elapsed < 5


def test_wait_above_limit_is_rejected(http, fresh_org):
    _, headers = make_worker(fresh_org)
    assert claim(http, headers, wait=26).status_code == 422


def test_protocol_two_heartbeat_is_refused(http, fresh_org):
    _, headers = make_worker(fresh_org)
    response = heartbeat(http, headers, protocol=2)
    assert response.status_code == 426
    assert error_code(response) == "protocol_incompatible"


def test_stale_lease_is_fenced_after_requeue(http, fresh_org, frozen):
    job_id = make_job(fresh_org)
    _, first = make_worker(fresh_org)
    _, second = make_worker(fresh_org)

    lease_a = claim(http, first).json()
    token_a = lease_a["lease_token"]
    assert lease_a["attempt"] == 1

    frozen(T0 + timedelta(seconds=121))
    with Session(engine) as session:
        assert broker.sweep_leases(session) == 1
    assert job_row(job_id).status == JobStatus.PENDING

    lease_b = claim(http, second).json()
    assert lease_b["attempt"] == 2

    stale_beat = http.post(f"/v1/workers/jobs/{job_id}/heartbeat",
                           headers=lease_headers(first, token_a))
    assert stale_beat.status_code == 409
    assert error_code(stale_beat) == "stale_lease"

    stale_result = post_result(http, job_id, first, token_a, result_body())
    assert stale_result.status_code == 409
    assert error_code(stale_result) == "stale_lease"
    assert job_row(job_id).status == JobStatus.PROCESSING

    accepted = post_result(http, job_id, second, lease_b["lease_token"], result_body())
    assert accepted.status_code == 200
    assert job_row(job_id).status == JobStatus.COMPLETED

    late = post_result(http, job_id, first, token_a, result_body(markdown="# late"))
    assert late.status_code == 409
    assert error_code(late) == "stale_lease"


def test_duplicate_result_same_lease_and_hash_is_idempotent(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]
    body = result_body()

    assert post_result(http, job_id, headers, token, body).status_code == 200
    assert post_result(http, job_id, headers, token, body).status_code == 200
    assert job_row(job_id).status == JobStatus.COMPLETED


def test_duplicate_result_same_lease_different_hash_conflicts(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    assert post_result(http, job_id, headers, token, result_body()).status_code == 200
    conflict = post_result(http, job_id, headers, token, result_body(markdown="# other"))
    assert conflict.status_code == 409
    assert error_code(conflict) == "result_conflict"


def test_cancel_after_claim_is_visible_and_rejects_result(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    set_job(job_id, cancel_requested=True)

    beat = http.post(f"/v1/workers/jobs/{job_id}/heartbeat", headers=lease_headers(headers, token))
    assert beat.status_code == 200
    assert beat.json()["cancel_requested"] is True

    rejected = post_result(http, job_id, headers, token, result_body())
    assert rejected.status_code == 409
    assert error_code(rejected) == "stale_lease"


def test_cancel_requested_job_is_not_claimed(http, fresh_org):
    make_job(fresh_org, cancel_requested=True)
    _, headers = make_worker(fresh_org)
    assert claim(http, headers).status_code == 204


def test_retryable_failures_requeue_until_attempts_exhausted(http, fresh_org):
    job_id = make_job(fresh_org, max_attempts=3)
    _, headers = make_worker(fresh_org)

    for attempt in (1, 2, 3):
        lease = claim(http, headers).json()
        assert lease["attempt"] == attempt
        fail = http.post(
            f"/v1/workers/jobs/{job_id}/fail",
            json={"code": "worker_restarted", "message": "restarted", "retryable": True},
            headers=lease_headers(headers, lease["lease_token"]),
        )
        assert fail.status_code == 200

    row = job_row(job_id)
    assert row.status == JobStatus.FAILED
    assert row.error_code == "attempts_exhausted"


def test_non_retryable_failure_is_terminal_with_given_code(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    lease = claim(http, headers).json()

    response = http.post(
        f"/v1/workers/jobs/{job_id}/fail",
        json={"code": "unsupported_input", "message": "cannot parse", "retryable": False},
        headers=lease_headers(headers, lease["lease_token"]),
    )
    assert response.json() == {"status": "failed"}
    assert job_row(job_id).error_code == "unsupported_input"


def test_cancel_during_failure_finishes_cancelled(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    lease = claim(http, headers).json()
    set_job(job_id, cancel_requested=True)

    response = http.post(
        f"/v1/workers/jobs/{job_id}/fail",
        json={"code": "worker_error", "message": "x", "retryable": True},
        headers=lease_headers(headers, lease["lease_token"]),
    )
    assert response.json() == {"status": "cancelled"}


def test_input_download_requires_current_lease(http, fresh_org):
    job_id = make_job(fresh_org, data=b"payload-bytes")
    _, headers = make_worker(fresh_org)
    lease = claim(http, headers).json()

    ok = http.get(f"/v1/workers/jobs/{job_id}/input",
                  headers=lease_headers(headers, lease["lease_token"]))
    assert ok.status_code == 200
    assert ok.content == b"payload-bytes"

    no_lease = http.get(f"/v1/workers/jobs/{job_id}/input", headers=headers)
    assert no_lease.status_code == 409
    assert error_code(no_lease) == "stale_lease"


def test_other_organization_cannot_touch_job(http, fresh_org, org_b):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    lease = claim(http, headers).json()
    _, foreign = make_worker(org_b)

    response = http.get(f"/v1/workers/jobs/{job_id}/input",
                        headers=lease_headers(foreign, lease["lease_token"]))
    assert response.status_code == 404
    assert job_row(job_id).status == JobStatus.PROCESSING


def test_result_rejects_invalid_body_checksum_and_kind(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    bad_shape = post_result(http, job_id, headers, token, b'{"not": "a result"}')
    assert bad_shape.status_code == 422
    assert error_code(bad_shape) == "invalid_result"

    mismatch = post_result(http, job_id, headers, token, result_body(),
                           sha=hashlib.sha256(b"other").hexdigest())
    assert mismatch.status_code == 422
    assert error_code(mismatch) == "checksum_mismatch"

    wrong_kind = post_result(http, job_id, headers, token, result_body(kind="document"))
    assert wrong_kind.status_code == 422
    assert error_code(wrong_kind) == "kind_mismatch"
    assert job_row(job_id).status == JobStatus.PROCESSING


def test_oversized_result_is_refused(http, fresh_org, monkeypatch):
    monkeypatch.setattr(worker_routes, "MAX_RESULT_BYTES", 64)
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    response = post_result(http, job_id, headers, token, result_body())
    assert response.status_code == 413
    assert job_row(job_id).status == JobStatus.PROCESSING


def test_completed_result_removes_input_and_sets_retention(http, fresh_org):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    assert post_result(http, job_id, headers, token, result_body()).status_code == 200

    row = job_row(job_id)
    assert row.input_deleted is True
    assert row.result_expires_at - row.completed_at == timedelta(hours=24)
    assert json.loads(row.result_manifest)["attempt"] == 1


def test_policy_check_denies_cloud_endpoint_for_private_job(http, fresh_org, store_settings):
    endpoint = Endpoint(id="cloud-llm", kind="cloud_llm", model="gpt-x",
                        url="https://llm.example.com/v1")
    store_settings(fresh_org, cloud_enabled=True, endpoints=[endpoint])
    make_job(fresh_org, private=True)
    _, headers = make_worker(fresh_org, private=True)
    token = claim(http, headers).json()["lease_token"]
    job_id = claim_job_id(http, headers, token)

    response = http.post(f"/v1/workers/jobs/{job_id}/policy",
                         json={"stage": "llm", "endpoint_id": "cloud-llm"},
                         headers=lease_headers(headers, token))
    assert response.status_code == 403
    assert error_code(response) == "policy_denied"


def claim_job_id(http, headers, token) -> int:
    """The job id of the lease already claimed with ``token`` (single pending job in the test)."""
    with Session(engine) as session:
        from sqlmodel import select
        job = session.exec(select(Job).where(Job.lease_token_hash == admin.hash_token(token))).one()
        return job.id


def test_policy_check_follows_current_cloud_setting_for_non_private_job(
    http, fresh_org, store_settings
):
    endpoint = Endpoint(id="cloud-llm", kind="cloud_llm", model="gpt-x",
                        url="https://llm.example.com/v1")
    store_settings(fresh_org, cloud_enabled=True, endpoints=[endpoint])
    make_job(fresh_org, private=False)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]
    job_id = claim_job_id(http, headers, token)

    allowed = http.post(f"/v1/workers/jobs/{job_id}/policy",
                        json={"stage": "llm", "endpoint_id": "cloud-llm"},
                        headers=lease_headers(headers, token))
    assert allowed.status_code == 200

    store_settings(fresh_org, cloud_enabled=False, endpoints=[endpoint])
    revoked = http.post(f"/v1/workers/jobs/{job_id}/policy",
                        json={"stage": "llm", "endpoint_id": "cloud-llm"},
                        headers=lease_headers(headers, token))
    assert revoked.status_code == 403
    assert error_code(revoked) == "policy_denied"


def test_policy_check_rejects_unknown_endpoint(http, fresh_org):
    make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]
    job_id = claim_job_id(http, headers, token)

    response = http.post(f"/v1/workers/jobs/{job_id}/policy",
                         json={"stage": "llm", "endpoint_id": "does-not-exist"},
                         headers=lease_headers(headers, token))
    assert response.status_code == 403
    assert error_code(response) == "policy_denied"


def test_heartbeat_reports_cancel_and_extends_lease(http, fresh_org, frozen):
    job_id = make_job(fresh_org)
    _, headers = make_worker(fresh_org)
    token = claim(http, headers).json()["lease_token"]

    frozen(T0 + timedelta(seconds=100))
    response = http.post(f"/v1/workers/jobs/{job_id}/heartbeat",
                         headers=lease_headers(headers, token))
    assert response.status_code == 200
    assert response.json()["cancel_requested"] is False
    assert job_row(job_id).lease_until.replace(tzinfo=UTC) == T0 + timedelta(seconds=220)
