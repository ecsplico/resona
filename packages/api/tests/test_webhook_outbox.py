"""Job webhooks: ``JobRequest.webhook`` admission and one outbox row per terminal transition.

Jobs are driven through the real client and worker routes (upload -> job -> claim ->
result/fail), as in the other API tests; the outbox rows are read from the database.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from release_approval import approve_release
from resona_job_core import Candidate
from resona_job_core.webhooks import JobEvent
from resona_api import audio_routes, broker, clock, jobs_routes, releases, webhooks
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, WebhookDelivery, WebhookEndpoint
from resona_api.releases import hub_release_id

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def make_endpoint(org_id: str, name: str = "app") -> str:
    with Session(engine) as session:
        endpoint = webhooks.new_endpoint(org_id, name, "https://hooks.example.org/resona")
        session.add(endpoint)
        session.commit()
        return endpoint.id


def revoke_endpoint(endpoint_id: str) -> None:
    with Session(engine) as session:
        endpoint = session.get(WebhookEndpoint, endpoint_id)
        endpoint.revoked_at = clock.now()
        session.add(endpoint)
        session.commit()


def upload(http, headers, data) -> str:
    resp = http.post("/v1/uploads", files={"file": ("a.wav", data, "audio/wav")}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["upload_id"]


def submit(http, headers, data, *, key=None, **fields):
    body = {"kind": "audio", "upload_id": upload(http, headers, data), **fields}
    return http.post("/v1/jobs", json=body, headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex})


def new_job(http, headers, data, **fields) -> int:
    resp = submit(http, headers, data, **fields)
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def make_worker(org_id: str) -> dict[str, str]:
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "outbox-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[MAC_AUDIO.model_dump(mode="json")]).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def claim(http, worker) -> dict:
    resp = http.post("/v1/workers/claim?wait=0",
                     json={"capabilities": [MAC_AUDIO.model_dump(mode="json")]}, headers=worker)
    assert resp.status_code == 200, resp.text
    return resp.json()


def post_result(http, worker, lease: dict):
    body = json.dumps({
        "schema_version": 1, "kind": "audio", "raw_text": "Hallo Welt",
        "markdown": "Hallo Welt", "provenance": {"stages": []},
    }).encode()
    return http.post(f"/v1/workers/jobs/{lease['job_id']}/result", content=body, headers={
        **worker, "X-Resona-Lease": lease["lease_token"], "Content-Type": "application/json",
        "X-Resona-Result-Sha256": hashlib.sha256(body).hexdigest(),
    })


def fail(http, worker, lease: dict, *, retryable: bool):
    return http.post(
        f"/v1/workers/jobs/{lease['job_id']}/fail",
        json={"code": "engine_error", "message": "the engine crashed on Hallo Welt", "retryable": retryable},
        headers={**worker, "X-Resona-Lease": lease["lease_token"]},
    )


def deliveries(job_id: int) -> list[WebhookDelivery]:
    with Session(engine) as session:
        return list(session.exec(select(WebhookDelivery).where(WebhookDelivery.job_id == job_id)).all())


def only_event(job_id: int) -> JobEvent:
    rows = deliveries(job_id)
    assert len(rows) == 1, rows
    event = JobEvent.model_validate_json(rows[0].payload_json)
    assert rows[0].event == event.type and rows[0].attempts == 0 and rows[0].delivered_at is None
    return event


def job_row(job_id: int) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


# ── Admission ────────────────────────────────────────────────────────────────

def test_job_view_names_the_webhook(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    endpoint_id = make_endpoint(fresh_org)
    with_hook = new_job(http, headers, wav_bytes, webhook="app")
    without = new_job(http, headers, wav_bytes)

    assert job_row(with_hook).webhook_endpoint_id == endpoint_id
    assert http.get(f"/v1/jobs/{with_hook}", headers=headers).json()["webhook"] == "app"
    assert http.get(f"/v1/jobs/{without}", headers=headers).json()["webhook"] is None
    items = {item["job_id"]: item["webhook"] for item in http.get("/v1/jobs", headers=headers).json()["items"]}
    assert items == {with_hook: "app", without: None}

    revoke_endpoint(endpoint_id)  # the job keeps the name it named
    assert http.get(f"/v1/jobs/{with_hook}", headers=headers).json()["webhook"] == "app"


def test_unknown_revoked_or_foreign_webhook_is_unknown_webhook(http, fresh_org, org_b, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    revoke_endpoint(make_endpoint(fresh_org, "gone"))
    make_endpoint(org_b, "theirs")

    for name in ("missing", "gone", "theirs", "Not A Name"):
        resp = submit(http, headers, wav_bytes, webhook=name)
        assert (resp.status_code, error_code(resp)) == (422, "unknown_webhook"), name
    with Session(engine) as session:
        assert session.exec(select(Job).where(Job.organization_id == fresh_org)).all() == []


def test_idempotency_replay_with_a_different_webhook_is_409(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org, "app")
    make_endpoint(fresh_org, "other")

    first = submit(http, headers, wav_bytes, key="hook-replay", webhook="app")
    assert first.status_code == 202
    same = submit(http, headers, wav_bytes, key="hook-replay", webhook="app")
    assert same.status_code == 202 and same.json()["job_id"] == first.json()["job_id"]
    for changed in ({"webhook": "other"}, {}):
        resp = submit(http, headers, wav_bytes, key="hook-replay", **changed)
        assert (resp.status_code, error_code(resp)) == (409, "idempotency_conflict")

    plain = submit(http, headers, wav_bytes, key="plain-replay")
    assert plain.status_code == 202
    resp = submit(http, headers, wav_bytes, key="plain-replay", webhook="app")
    assert (resp.status_code, error_code(resp)) == (409, "idempotency_conflict")


def test_request_hash_without_webhook_is_unchanged():
    # Keys recorded before webhooks existed hashed a request without the field.
    req = jobs_routes.JobRequest(kind="audio", upload_id="upl_x")
    fields = req.model_dump(mode="json")
    fields.pop("webhook")
    fields["upload_id"] = None
    fields["input_sha256"] = "upl_x"
    expected = hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert jobs_routes._request_hash(req, None) == expected
    hooked = jobs_routes.JobRequest(kind="audio", upload_id="upl_x", webhook="app")
    assert jobs_routes._request_hash(hooked, None) != expected


def test_evaluation_jobs_refuse_a_webhook(http, fresh_org, admin_headers, wav_bytes):
    make_endpoint(fresh_org)
    approve_release(fresh_org)  # records the bundled release
    resp = submit(http, admin_headers(fresh_org), wav_bytes, webhook="app",
                  purpose="evaluation", release_id=hub_release_id())
    assert (resp.status_code, error_code(resp)) == (422, "invalid_request")


def test_transcriptions_form_field_webhook(http, fresh_org, client_headers, wav_bytes, monkeypatch):
    monkeypatch.setattr(audio_routes, "SYNC_WAIT_SECONDS", 0.1)
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    files = {"file": ("a.wav", wav_bytes, "audio/wav")}

    unknown = http.post("/v1/audio/transcriptions", files=files, data={"webhook": "missing"}, headers=headers)
    assert (unknown.status_code, error_code(unknown)) == (422, "unknown_webhook")

    resp = http.post("/v1/audio/transcriptions", files=files, data={"webhook": "app"},
                     headers={**headers, "Idempotency-Key": "sync-hook"})
    assert resp.status_code == 504
    job_id = resp.json()["job_id"]
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).json()["webhook"] == "app"
    replay = http.post("/v1/audio/transcriptions", files=files,
                       headers={**headers, "Idempotency-Key": "sync-hook"})
    assert (replay.status_code, error_code(replay)) == (409, "idempotency_conflict")


# ── One row per terminal transition ──────────────────────────────────────────

def test_completed_job_writes_one_completed_event(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    endpoint_id = make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    worker = make_worker(fresh_org)
    lease = claim(http, worker)
    assert deliveries(job_id) == []  # claiming is not terminal

    assert post_result(http, worker, lease).status_code == 200
    assert post_result(http, worker, lease).status_code == 200  # accepted duplicate: no second row
    event = only_event(job_id)
    assert (event.type, event.job_id, event.kind, event.status, event.error_code) == (
        "job.completed", job_id, "audio", "completed", None)
    row = deliveries(job_id)[0]
    assert (row.organization_id, row.endpoint_id) == (fresh_org, endpoint_id)
    assert "Hallo" not in row.payload_json and "a.wav" not in row.payload_json


def test_failed_job_writes_one_failed_event_only_when_it_ends(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    worker = make_worker(fresh_org)

    assert fail(http, worker, claim(http, worker), retryable=True).status_code == 200
    assert job_row(job_id).status == JobStatus.PENDING and deliveries(job_id) == []  # requeued

    assert fail(http, worker, claim(http, worker), retryable=False).status_code == 200
    event = only_event(job_id)
    assert (event.type, event.status, event.error_code) == ("job.failed", "failed", "engine_error")
    assert "crashed" not in deliveries(job_id)[0].payload_json  # the message never leaves the hub


def test_cancelled_pending_job_writes_one_cancelled_event(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")

    assert http.post(f"/v1/jobs/{job_id}/cancel", headers=headers).json()["status"] == "cancelled"
    assert http.post(f"/v1/jobs/{job_id}/cancel", headers=headers).status_code == 200  # no second transition
    event = only_event(job_id)
    assert (event.type, event.status, event.error_code) == ("job.cancelled", "cancelled", "cancelled")


def test_cancelled_processing_job_writes_one_event_when_the_worker_lets_go(
    http, fresh_org, client_headers, wav_bytes
):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    worker = make_worker(fresh_org)
    lease = claim(http, worker)

    assert http.post(f"/v1/jobs/{job_id}/cancel", headers=headers).json()["status"] == "processing"
    assert deliveries(job_id) == []
    assert fail(http, worker, lease, retryable=True).status_code == 200
    assert only_event(job_id).type == "job.cancelled"


def test_sweep_failures_write_one_event(http, fresh_org, client_headers, wav_bytes, monkeypatch):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")

    later = clock.now() + timedelta(hours=25)
    monkeypatch.setattr(clock, "now", lambda: later)
    for _ in range(2):
        with Session(engine) as session:
            broker.sweep_leases(session)
    event = only_event(job_id)
    assert (event.type, event.error_code) == ("job.failed", "deadline_exceeded")


def test_deleting_a_processing_job_writes_cancelled_once_and_the_purge_nothing(
    http, fresh_org, client_headers, wav_bytes, monkeypatch
):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    worker = make_worker(fresh_org)
    lease = claim(http, worker)

    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204
    event = only_event(job_id)
    assert (event.type, event.status, event.error_code) == ("job.cancelled", "cancelled", broker.JOB_DELETED)

    late = fail(http, worker, lease, retryable=False)  # the fenced worker cannot end it again
    assert late.status_code == 409
    with Session(engine) as session:
        broker.sweep_leases(session)
    later = clock.now() + timedelta(seconds=broker.LEASE_SECONDS + 1)
    monkeypatch.setattr(clock, "now", lambda: later)
    with Session(engine) as session:
        broker.sweep_leases(session)
    assert job_row(job_id) is None  # tombstone purged
    assert len(deliveries(job_id)) == 1


def test_deleting_a_pending_job_writes_cancelled_once(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204
    assert job_row(job_id) is None
    assert only_event(job_id).type == "job.cancelled"


def test_deleting_a_finished_job_writes_nothing_more(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204
    assert len(deliveries(job_id)) == 1


def test_endpoint_revoked_after_admission_gets_no_event(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    revoke_endpoint_id = make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    revoke_endpoint(revoke_endpoint_id)
    http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert deliveries(job_id) == []


# ── Jobs that never notify ───────────────────────────────────────────────────

def test_job_without_webhook_writes_nothing(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    worker = make_worker(fresh_org)
    completed = new_job(http, headers, wav_bytes)
    assert post_result(http, worker, claim(http, worker)).status_code == 200
    failed = new_job(http, headers, wav_bytes)
    assert fail(http, worker, claim(http, worker), retryable=False).status_code == 200
    cancelled = new_job(http, headers, wav_bytes)
    http.post(f"/v1/jobs/{cancelled}/cancel", headers=headers)

    assert [job_row(j).status for j in (completed, failed, cancelled)] == [
        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
    with Session(engine) as session:
        assert session.exec(select(WebhookDelivery).where(WebhookDelivery.organization_id == fresh_org)).all() == []


def test_evaluation_job_writes_nothing(http, fresh_org, admin_headers, wav_bytes):
    make_endpoint(fresh_org)
    worker = make_worker(fresh_org)
    resp = submit(http, admin_headers(fresh_org), wav_bytes, purpose="evaluation", release_id=hub_release_id())
    assert resp.status_code == 202, resp.text
    lease = claim(http, worker)
    assert lease["job_id"] == resp.json()["job_id"]
    assert post_result(http, worker, lease).status_code == 200
    assert deliveries(lease["job_id"]) == []


@pytest.mark.parametrize("purpose", ["evaluation", "testbench"])
def test_non_production_jobs_never_write_even_with_an_endpoint(
    http, fresh_org, client_headers, wav_bytes, purpose
):
    # Only production admission accepts ``webhook``; the outbox refuses other purposes too.
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    job_id = new_job(http, headers, wav_bytes, webhook="app")
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.purpose = purpose
        session.add(job)
        session.commit()
    http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert job_row(job_id).status == JobStatus.CANCELLED
    assert deliveries(job_id) == []


def test_testbench_job_writes_nothing(http, fresh_org, admin_headers, store_settings, wav_bytes):
    store_settings(fresh_org, testbench_enabled=True)
    make_endpoint(fresh_org)
    worker = make_worker(fresh_org)
    beat = http.post("/v1/workers/heartbeat", headers=worker, json={
        "worker_protocol": 1, "runtime_version": hub_release_id(), "ready": True,
        "reported_capabilities": [MAC_AUDIO.model_dump(mode="json")],
    })
    assert beat.status_code == 200
    item = http.post("/v1/testbench/items", files={"file": ("memo.wav", wav_bytes, "audio/wav")},
                     data={"language": "de"}, headers=admin_headers(fresh_org))
    assert item.status_code in (200, 201), item.text
    lease = claim(http, worker)
    assert job_row(lease["job_id"]).purpose == jobs_routes.TESTBENCH_PURPOSE
    assert post_result(http, worker, lease).status_code == 200
    assert deliveries(lease["job_id"]) == []


# ── Workers stay unchanged ───────────────────────────────────────────────────

def test_previous_release_worker_runs_a_webhook_job_with_an_unchanged_snapshot(
    http, fresh_org, client_headers, wav_bytes
):
    headers = client_headers(fresh_org)
    make_endpoint(fresh_org)
    release_id = f"0.1.0+t{uuid.uuid4().hex[:10]}"
    manifest = {
        "release_id": release_id, "created_at": "2026-11-01T10:00:00Z",
        "wheels": [{"name": "resona-worker", "version": release_id, "sha256": "b" * 64}],
        "images": [{"name": "ghcr.io/ecsplico/resona-hub", "digest": "sha256:" + "a" * 64}],
        "models": [], "worker_protocol": 1,
    }
    with Session(engine) as session:
        releases.stage_release(session, json.dumps(manifest).encode())
    approve_release(fresh_org, release_id=release_id)
    worker = make_worker(fresh_org)
    beat = http.post("/v1/workers/heartbeat", headers=worker, json={  # an old release reports no features
        "worker_protocol": 1, "runtime_version": release_id, "ready": True,
        "reported_capabilities": [MAC_AUDIO.model_dump(mode="json")],
    })
    assert beat.status_code == 200

    plain = new_job(http, headers, wav_bytes)
    hooked = new_job(http, headers, wav_bytes, webhook="app")
    snapshots = {job_id: json.loads(job_row(job_id).execution_snapshot) for job_id in (plain, hooked)}
    assert snapshots[plain] == snapshots[hooked]
    assert broker.job_features(snapshots[hooked], "audio") == broker.job_features(snapshots[plain], "audio")

    assert http.get(f"/v1/jobs/{hooked}", headers=headers).json()["queue_reason"] == "waiting_capacity"
    first = claim(http, worker)
    assert first["job_id"] == plain and post_result(http, worker, first).status_code == 200
    second = claim(http, worker)
    assert second["job_id"] == hooked
    assert "webhook" not in json.dumps(second["execution_snapshot"])
    assert post_result(http, worker, second).status_code == 200
    assert only_event(hooked).type == "job.completed"
    assert deliveries(plain) == []
