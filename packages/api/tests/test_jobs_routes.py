"""Behavioral tests for the client job routes (/v1/uploads, /v1/jobs...)."""

import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from resona_job_core import Candidate, Endpoint, default_settings
from resona_api import clock, jobs_routes, storage
from resona_api.admin import approve_worker, enroll_worker, revoke_credential, revoke_worker
from resona_api.releases import hub_release_id
from release_approval import approve_release
from resona_api.auth import hash_token
from resona_api.db.engine import engine
from resona_api.db.models import Credential, Job, JobStatus, Organization

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
DEEPGRAM = Endpoint(id="cloud-asr:deepgram", kind="cloud_asr", model="nova-3")
PDF = b"%PDF-1.4\n% synthetic\n"


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def upload(http, headers, data, *, name="a.wav", ctype="audio/wav"):
    return http.post("/v1/uploads", files={"file": (name, data, ctype)}, headers=headers)


def submit(http, headers, body, *, key=None):
    return http.post("/v1/jobs", json=body, headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex})


def new_job(http, headers, data, **fields) -> int:
    upload_id = upload(http, headers, data).json()["upload_id"]
    resp = submit(http, headers, {"kind": "audio", "upload_id": upload_id, **fields})
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def make_worker(org_id, *, capabilities=(MAC_AUDIO,)):
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "jobs-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[c.model_dump(mode="json") for c in capabilities]
                       ).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return worker_id, {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def claim(http, headers, caps=(MAC_AUDIO,)):
    return http.post("/v1/workers/claim?wait=0",
                     json={"capabilities": [c.model_dump(mode="json") for c in caps]}, headers=headers)


def post_result(http, job_id, headers, lease, markdown="# Hallo Welt"):
    body = json.dumps({
        "schema_version": 1, "kind": "audio", "raw_text": "Hallo Welt",
        "markdown": markdown, "provenance": {"stages": []},
    }).encode()
    return http.post(f"/v1/workers/jobs/{job_id}/result", content=body, headers={
        **headers, "X-Resona-Lease": lease, "Content-Type": "application/json",
        "X-Resona-Result-Sha256": hashlib.sha256(body).hexdigest(),
    })


def storage_id(org_id) -> str:
    with Session(engine) as session:
        return session.get(Organization, org_id).storage_id


def job_row(job_id) -> Job:
    with Session(engine) as session:
        return session.get(Job, job_id)


# ── Roundtrip ────────────────────────────────────────────────────────────────

def test_upload_submit_claim_result_roundtrip(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    up = upload(http, headers, wav_bytes)
    assert up.status_code == 201
    assert up.json()["sha256"] == hashlib.sha256(wav_bytes).hexdigest()
    assert up.json()["size"] == len(wav_bytes)

    resp = submit(http, headers, {"kind": "audio", "upload_id": up.json()["upload_id"]})
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"
    job_id = resp.json()["job_id"]
    view = http.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert view["queue_reason"] == "no_capable_worker"
    assert view["private"] is True and view["execution_profile"] == "private-auto"
    assert storage.job_input_path(storage_id(fresh_org), job_id).is_file()
    assert error_code(http.get(f"/v1/jobs/{job_id}/result", headers=headers)) == "not_ready"

    _, worker = make_worker(fresh_org)
    granted = claim(http, worker)
    assert granted.status_code == 200
    claimed = granted.json()
    assert claimed["job_id"] == job_id
    assert claimed["input_sha256"] == hashlib.sha256(wav_bytes).hexdigest()
    snapshot = claimed["execution_snapshot"]
    assert snapshot["candidate"] == MAC_AUDIO.model_dump(mode="json")
    assert snapshot["language"] == "de" and snapshot["policy"]["private"] is True
    downloaded = http.get(f"/v1/workers/jobs/{job_id}/input",
                          headers={**worker, "X-Resona-Lease": claimed["lease_token"]})
    assert downloaded.content == wav_bytes

    assert post_result(http, job_id, worker, claimed["lease_token"]).status_code == 200
    result = http.get(f"/v1/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200
    assert result.json()["markdown"] == "# Hallo Welt"
    view = http.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert view["status"] == "completed" and view["result_available"] is True
    assert not storage.job_input_path(storage_id(fresh_org), job_id).exists()


def _heartbeat(http, worker, reported):
    beat = http.post("/v1/workers/heartbeat", headers=worker, json={
        "worker_protocol": 1, "runtime_version": "0.1.0", "ready": bool(reported),
        "reported_capabilities": [c.model_dump(mode="json") for c in reported],
    })
    assert beat.status_code == 200


def test_queue_reason_waiting_capacity_when_capable_worker_online(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    _heartbeat(http, worker, [MAC_AUDIO])
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).json()["queue_reason"] == "waiting_capacity"


def test_queue_reason_ignores_approved_tuples_the_worker_does_not_offer(http, fresh_org, client_headers, wav_bytes):
    # An approved CUDA worker started without a GPU heartbeats with no tuples: it cannot
    # claim, so the job waits for another worker (no_capable_worker), not for its capacity.
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    _heartbeat(http, worker, [])
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).json()["queue_reason"] == "no_capable_worker"
    assert claim(http, worker, caps=()).status_code == 204


def test_result_expires_after_retention(http, fresh_org, client_headers, wav_bytes, monkeypatch):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    lease = claim(http, worker).json()["lease_token"]
    assert post_result(http, job_id, worker, lease).status_code == 200
    later = clock.now() + timedelta(hours=25)
    monkeypatch.setattr(clock, "now", lambda: later)
    resp = http.get(f"/v1/jobs/{job_id}/result", headers=headers)
    assert resp.status_code == 410
    assert error_code(resp) == "result_expired"


# ── Idempotency and upload consumption ───────────────────────────────────────

def test_idempotent_replay_returns_same_job_and_changed_body_conflicts(http, fresh_org, client_headers,
                                                                      wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    body = {"kind": "audio", "upload_id": upload_id}
    first = submit(http, headers, body, key="key-1")
    replay = submit(http, headers, body, key="key-1")
    assert first.status_code == replay.status_code == 202
    assert replay.json()["job_id"] == first.json()["job_id"]

    changed = submit(http, headers, {**body, "language": "en"}, key="key-1")
    assert changed.status_code == 409
    assert error_code(changed) == "idempotency_conflict"
    assert len(http.get("/v1/jobs", headers=headers).json()["items"]) == 1


def test_replay_after_reupload_of_same_file_returns_same_job(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    first_upload = upload(http, headers, wav_bytes).json()["upload_id"]
    first = submit(http, headers, {"kind": "audio", "upload_id": first_upload}, key="key-2")
    second_upload = upload(http, headers, wav_bytes).json()["upload_id"]
    replay = submit(http, headers, {"kind": "audio", "upload_id": second_upload}, key="key-2")
    assert replay.status_code == 202
    assert replay.json()["job_id"] == first.json()["job_id"]

    other_file = upload(http, headers, wav_bytes + b"\x00\x00").json()["upload_id"]
    conflict = submit(http, headers, {"kind": "audio", "upload_id": other_file}, key="key-2")
    assert conflict.status_code == 409 and error_code(conflict) == "idempotency_conflict"



def test_missing_idempotency_key_is_400(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    resp = http.post("/v1/jobs", json={"kind": "audio", "upload_id": upload_id}, headers=headers)
    assert resp.status_code == 400
    assert error_code(resp) == "idempotency_key_required"


def test_upload_is_consumed_by_exactly_one_job(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    assert submit(http, headers, {"kind": "audio", "upload_id": upload_id}).status_code == 202
    second = submit(http, headers, {"kind": "audio", "upload_id": upload_id})
    assert second.status_code == 404
    assert error_code(second) == "upload_not_found"


def _link_upload(http, link, data, *, name="a.wav", ctype="audio/wav"):
    return http.post("/v1/upload-links/upload", files={"file": (name, data, ctype)},
                     headers={link["token_header"]: link["token"]})


def test_upload_link_delivers_one_file_into_the_minting_org(http, fresh_org, org_b, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    link = http.post("/v1/upload-links", headers=headers).json()
    up = _link_upload(http, link, wav_bytes)
    assert up.status_code == 201
    assert up.json()["sha256"] == hashlib.sha256(wav_bytes).hexdigest()

    again = _link_upload(http, link, wav_bytes)
    assert again.status_code == 401 and error_code(again) == "invalid_upload_link"

    other = submit(http, client_headers(org_b), {"kind": "audio", "upload_id": up.json()["upload_id"]})
    assert other.status_code == 404
    assert submit(http, headers, {"kind": "audio", "upload_id": up.json()["upload_id"]}).status_code == 202


def test_upload_link_is_spent_by_a_rejected_attempt(http, fresh_org, client_headers, wav_bytes):
    link = http.post("/v1/upload-links", headers=client_headers(fresh_org)).json()
    assert _link_upload(http, link, b"not media", name="a.txt", ctype="text/plain").status_code == 415
    assert _link_upload(http, link, wav_bytes).status_code == 401


def test_upload_link_refused_after_expiry_or_credential_revocation(http, fresh_org, client_headers,
                                                                  wav_bytes, monkeypatch):
    headers = client_headers(fresh_org)
    expiring = http.post("/v1/upload-links", headers=headers).json()
    revoked = http.post("/v1/upload-links", headers=headers).json()
    with Session(engine) as session:
        cred = session.exec(select(Credential).where(Credential.organization_id == fresh_org)).one()
        revoke_credential(session, cred.id)
        session.commit()
    assert _link_upload(http, revoked, wav_bytes).status_code == 401

    later = clock.now() + jobs_routes.UPLOAD_LINK_TTL + timedelta(seconds=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert _link_upload(http, expiring, wav_bytes).status_code == 401


def test_upload_link_minting_needs_a_client_credential(http, fresh_org, worker_headers, wav_bytes):
    assert http.post("/v1/upload-links").status_code == 401
    assert http.post("/v1/upload-links", headers=worker_headers(fresh_org)).status_code == 403
    assert http.post("/v1/upload-links/upload", files={"file": ("a.wav", wav_bytes, "audio/wav")}).status_code == 401


def test_upload_kind_must_match_job_kind(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    resp = submit(http, headers, {"kind": "document", "upload_id": upload_id})
    assert resp.status_code == 422


def test_document_upload_is_admitted_as_document_job(http, fresh_org, client_headers):
    headers = client_headers(fresh_org)
    up = upload(http, headers, PDF, name="scan.pdf", ctype="application/pdf")
    assert up.status_code == 201
    resp = submit(http, headers, {"kind": "document", "upload_id": up.json()["upload_id"],
                                  "options": {"languages": ["de"], "ocr": "auto"}})
    assert resp.status_code == 202
    snapshot = json.loads(job_row(resp.json()["job_id"]).execution_snapshot)
    assert snapshot["kind"] == "document"
    assert snapshot["options"] == {"languages": ["de"], "ocr": "auto"}


def _submit_document(http, headers, **fields):
    upload_id = upload(http, headers, PDF, name="scan.pdf", ctype="application/pdf").json()["upload_id"]
    return submit(http, headers, {"kind": "document", "upload_id": upload_id, **fields})


def _snapshot_ocr_engines(resp) -> list[str]:
    snapshot = json.loads(job_row(resp.json()["job_id"]).execution_snapshot)
    return [c["ocr_engine"] for c in snapshot["candidates"]]


@pytest.mark.parametrize(
    "profile, fields",
    [
        ("private-auto", {"options": {"ocr": "off", "image_scale": 2.0}}),
        ("private-auto", {"options": {"ocr": "off", "ocr_min_confidence": 0.6}}),
        ("mac-private", {"options": {"ocr_min_confidence": 0.6}}),
        ("tesseract-cpu", {"options": {"ocr_min_confidence": 0.6}}),
        ("linux-cuda", {"options": {"languages": ["de", "ko"]}}),
        ("linux-cuda", {"language": "ko"}),
    ],
)
def test_document_options_that_no_candidate_can_run_are_rejected_at_submission(
    http, fresh_org, client_headers, store_settings, profile, fields
):
    profiles = {name: p.model_dump(mode="json") for name, p in default_settings().execution_profiles.items()}
    profiles["tesseract-cpu"] = {"document": [{"engine": "docling", "model": "docling-standard",
                                               "accelerator": "cpu", "ocr_engine": "tesseract"}]}
    store_settings(fresh_org, execution_profiles=profiles)
    headers = client_headers(fresh_org)

    resp = _submit_document(http, headers, execution_profile=profile, **fields)

    assert resp.status_code == 422
    assert error_code(resp) == "invalid_options"
    assert http.get("/v1/jobs", headers=headers).json()["items"] == []


def test_confidence_threshold_keeps_only_the_rapidocr_candidate(http, fresh_org, client_headers):
    resp = _submit_document(http, client_headers(fresh_org), execution_profile="private-auto",
                            options={"ocr_min_confidence": 0.6})

    assert resp.status_code == 202
    assert _snapshot_ocr_engines(resp) == ["rapidocr"]


def test_korean_document_is_routed_to_the_ocrmac_candidate(http, fresh_org, client_headers):
    resp = _submit_document(http, client_headers(fresh_org), execution_profile="private-auto",
                            options={"languages": ["ko"]})

    assert resp.status_code == 202
    assert _snapshot_ocr_engines(resp) == ["ocrmac"]


# ── Admission policy and settings resolution ─────────────────────────────────

def _cloud_profile_settings():
    profiles = {name: p.model_dump(mode="json") for name, p in default_settings().execution_profiles.items()}
    profiles["cloud"] = {"audio": [{"engine": "deepgram", "model": "nova-3", "accelerator": "cpu"}],
                         "document": []}
    return profiles


def test_private_job_with_cloud_only_candidate_is_policy_denied(http, fresh_org, client_headers,
                                                                store_settings, wav_bytes):
    store_settings(fresh_org, cloud_enabled=True, endpoints=[DEEPGRAM.model_dump(mode="json")],
                   execution_profiles=_cloud_profile_settings())
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    denied = submit(http, headers, {"kind": "audio", "upload_id": upload_id, "execution_profile": "cloud"})
    assert denied.status_code == 403
    assert error_code(denied) == "policy_denied"
    assert http.get("/v1/jobs", headers=headers).json()["items"] == []

    allowed = submit(http, headers, {"kind": "audio", "upload_id": upload_id,
                                     "execution_profile": "cloud", "private": False})
    assert allowed.status_code == 202


def test_non_private_job_denied_when_org_cloud_disabled(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    resp = submit(http, headers, {"kind": "audio", "upload_id": upload_id, "private": False})
    assert resp.status_code == 403
    assert error_code(resp) == "policy_denied"


def test_private_job_with_cloud_llm_postprocess_is_policy_denied(http, fresh_org, client_headers,
                                                                 store_profile, wav_bytes):
    store_profile(fresh_org, "cloud-polish", {
        "name": "cloud-polish", "steps": [{"type": "llm", "model": "gpt-4o-mini", "prompt": "x"}],
    })
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    resp = submit(http, headers, {"kind": "audio", "upload_id": upload_id, "profile": "cloud-polish"})
    assert resp.status_code == 403
    assert error_code(resp) == "policy_denied"


def test_stored_postprocess_profile_is_snapshotted(http, fresh_org, client_headers, store_profile, wav_bytes):
    store_profile(fresh_org, "dictation", {"name": "dictation", "steps": []})
    revision = store_profile(fresh_org, "dictation", {"name": "dictation", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
    ]})
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes, profile="dictation")
    postprocess = json.loads(job_row(job_id).execution_snapshot)["postprocess"]
    assert postprocess["name"] == "dictation" and postprocess["revision"] == revision
    assert set(postprocess) == {"name", "revision", "sha256"}


LOCAL_LLM = Endpoint(id="local-llm", kind="local_llm", model="qwen-local", url="http://127.0.0.1:9/v1")
SECRET_PROMPT = "Rewrite the dictation of patient Erika Mustermann carefully."


def _llm_profile_job(http, org, client_headers, store_settings, store_profile, wav_bytes) -> int:
    store_settings(org, endpoints=[LOCAL_LLM.model_dump(mode="json")])
    store_profile(org, "polish", {"name": "polish", "steps": [
        {"type": "llm", "name": "polish", "prompt": SECRET_PROMPT, "endpoint": "local-llm"},
    ]})
    return new_job(http, client_headers(org), wav_bytes, profile="polish")


def test_llm_profile_prompt_lives_in_leased_artifact_not_snapshot(http, fresh_org, client_headers,
                                                                 store_settings, store_profile, wav_bytes):
    job_id = _llm_profile_job(http, fresh_org, client_headers, store_settings, store_profile, wav_bytes)
    stored = job_row(job_id).execution_snapshot
    assert SECRET_PROMPT not in stored and "prompt" not in stored
    meta = json.loads(stored)["postprocess"]
    profile_file = storage.postprocess_path(storage_id(fresh_org), job_id)
    assert profile_file.is_file()

    _, worker = make_worker(fresh_org)
    assert error_code(http.get(f"/v1/workers/jobs/{job_id}/postprocess", headers=worker)) == "stale_lease"
    claimed = claim(http, worker).json()
    assert SECRET_PROMPT not in json.dumps(claimed["execution_snapshot"])
    lease = {**worker, "X-Resona-Lease": claimed["lease_token"]}
    resp = http.get(f"/v1/workers/jobs/{job_id}/postprocess", headers={**worker, "X-Resona-Lease": "wrong"})
    assert resp.status_code == 409
    resp = http.get(f"/v1/workers/jobs/{job_id}/postprocess", headers=lease)
    assert resp.status_code == 200
    assert hashlib.sha256(resp.content).hexdigest() == meta["sha256"]
    assert json.loads(resp.content)["steps"][0]["prompt"] == SECRET_PROMPT

    assert post_result(http, job_id, worker, claimed["lease_token"]).status_code == 200
    assert not profile_file.exists()


def test_cancel_removes_postprocess_artifact(http, fresh_org, client_headers, store_settings,
                                             store_profile, wav_bytes):
    job_id = _llm_profile_job(http, fresh_org, client_headers, store_settings, store_profile, wav_bytes)
    profile_file = storage.postprocess_path(storage_id(fresh_org), job_id)
    assert profile_file.is_file()
    resp = http.post(f"/v1/jobs/{job_id}/cancel", headers=client_headers(fresh_org))
    assert resp.json()["status"] == "cancelled"
    assert not profile_file.exists()


def test_unknown_profiles_are_404_and_unapproved_override_422(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    base = {"kind": "audio", "upload_id": upload_id}
    assert submit(http, headers, {**base, "execution_profile": "nope"}).status_code == 404
    assert submit(http, headers, {**base, "profile": "nope"}).status_code == 404
    assert submit(http, headers, {**base, "engine": "faster-whisper"}).status_code == 422
    assert submit(http, headers, {**base, "options": {"temperature": 1}}).status_code == 422


def test_engine_override_narrows_candidates(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes, engine="mlx-whisper")
    snapshot = json.loads(job_row(job_id).execution_snapshot)
    assert snapshot["candidates"] == [MAC_AUDIO.model_dump(mode="json")]


def test_settings_change_does_not_alter_existing_job(http, fresh_org, client_headers, store_settings,
                                                     wav_bytes):
    headers = client_headers(fresh_org)
    old = new_job(http, headers, wav_bytes)
    store_settings(fresh_org, language="en")
    new = new_job(http, headers, wav_bytes)
    assert json.loads(job_row(old).execution_snapshot)["language"] == "de"
    assert json.loads(job_row(new).execution_snapshot)["language"] == "en"


# ── Organization isolation ───────────────────────────────────────────────────

def test_other_org_gets_404_and_cannot_list_or_consume(http, fresh_org, client_headers, wav_bytes):
    a = client_headers(fresh_org)
    other = f"org-{uuid.uuid4().hex[:12]}"
    from resona_api.admin import create_organization
    with Session(engine) as session:
        create_organization(session, other)
    b = client_headers(other)

    job_id = new_job(http, a, wav_bytes)
    for method, path in (("GET", f"/v1/jobs/{job_id}"), ("GET", f"/v1/jobs/{job_id}/result"),
                         ("POST", f"/v1/jobs/{job_id}/cancel"), ("DELETE", f"/v1/jobs/{job_id}")):
        resp = http.request(method, path, headers=b)
        assert resp.status_code == 404, (method, path)
    assert http.get("/v1/jobs", headers=b).json()["items"] == []

    foreign_upload = upload(http, a, wav_bytes).json()["upload_id"]
    resp = submit(http, b, {"kind": "audio", "upload_id": foreign_upload})
    assert resp.status_code == 404
    assert job_row(job_id).status == JobStatus.PENDING


# ── Listing ──────────────────────────────────────────────────────────────────

def test_list_is_newest_first_with_cursor_pagination(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    ids = [new_job(http, headers, wav_bytes) for _ in range(3)]
    first = http.get("/v1/jobs?limit=2", headers=headers).json()
    assert [item["job_id"] for item in first["items"]] == [ids[2], ids[1]]
    assert first["next_cursor"]
    second = http.get(f"/v1/jobs?limit=2&cursor={first['next_cursor']}", headers=headers).json()
    assert [item["job_id"] for item in second["items"]] == [ids[0]]
    assert second["next_cursor"] is None
    assert http.get("/v1/jobs?limit=101", headers=headers).status_code == 422
    assert http.get("/v1/jobs?cursor=!!", headers=headers).status_code == 422


# ── Cancel and delete ────────────────────────────────────────────────────────

def test_cancel_pending_job_removes_input(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    resp = http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert not storage.job_input_path(storage_id(fresh_org), job_id).exists()
    _, worker = make_worker(fresh_org)
    assert claim(http, worker).status_code == 204


def test_cancel_processing_job_fences_lease(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    lease = claim(http, worker).json()["lease_token"]
    resp = http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "processing"
    late = post_result(http, job_id, worker, lease)
    assert late.status_code == 409
    assert error_code(late) == "stale_lease"
    assert job_row(job_id).status != JobStatus.COMPLETED


def test_delete_pending_job_removes_everything_at_once(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)

    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204

    assert http.get(f"/v1/jobs/{job_id}", headers=headers).status_code == 404
    assert job_row(job_id) is None
    assert not storage.job_dir(storage_id(fresh_org), job_id).exists()
    _, worker = make_worker(fresh_org)
    assert claim(http, worker).status_code == 204


def test_delete_processing_job_fences_lease_and_denies_reads_immediately(
    http, fresh_org, client_headers, wav_bytes
):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    lease = claim(http, worker).json()["lease_token"]
    fenced = {**worker, "X-Resona-Lease": lease}

    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204

    for path in (f"/v1/jobs/{job_id}", f"/v1/jobs/{job_id}/result", f"/v1/jobs/{job_id}/result/markdown"):
        assert http.get(path, headers=headers).status_code == 404, path
    assert http.post(f"/v1/jobs/{job_id}/cancel", headers=headers).status_code == 404
    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 404
    assert job_id not in [item["job_id"] for item in http.get("/v1/jobs", headers=headers).json()["items"]]
    assert not storage.job_dir(storage_id(fresh_org), job_id).exists()
    for method, path in (
        ("get", f"/v1/workers/jobs/{job_id}/input"),
        ("post", f"/v1/workers/jobs/{job_id}/heartbeat"),
    ):
        late = getattr(http, method)(path, headers=fenced)
        assert (late.status_code, error_code(late)) == (409, "stale_lease"), path
    late = post_result(http, job_id, worker, lease)
    assert (late.status_code, error_code(late)) == (409, "stale_lease")
    assert job_row(job_id).status == JobStatus.CANCELLED


def test_deleted_job_tombstone_is_purged_once_its_lease_would_have_expired(
    http, fresh_org, client_headers, wav_bytes, monkeypatch
):
    from resona_api import broker

    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    claim(http, worker)
    http.delete(f"/v1/jobs/{job_id}", headers=headers)
    with Session(engine) as session:
        broker.sweep_leases(session)
    assert job_row(job_id) is not None  # lease still live: keep the fence

    later = clock.now() + timedelta(seconds=broker.LEASE_SECONDS + 1)
    monkeypatch.setattr(clock, "now", lambda: later)
    with Session(engine) as session:
        broker.sweep_leases(session)
    assert job_row(job_id) is None


# ── Bounded Markdown reads ───────────────────────────────────────────────────

def _completed_job(http, fresh_org, headers, wav_bytes, markdown):
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    lease = claim(http, worker).json()["lease_token"]
    assert post_result(http, job_id, worker, lease, markdown=markdown).status_code == 200
    return job_id


def test_markdown_range_pages_through_the_result(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    markdown = "# Überschrift\n" + "äöü-" * 20_000
    job_id = _completed_job(http, fresh_org, headers, wav_bytes, markdown)

    chunks, offset = [], 0
    while offset is not None:
        page = http.get(f"/v1/jobs/{job_id}/result/markdown", params={"offset": offset}, headers=headers)
        assert page.status_code == 200
        body = page.json()
        assert len(body["text"]) <= jobs_routes.MAX_MARKDOWN_CHUNK_CHARS
        assert body["total_chars"] == len(markdown)
        assert "raw_text" not in body and "segments" not in body
        chunks.append(body["text"])
        offset = body["next_offset"]
    assert len(chunks) == 3
    assert "".join(chunks) == markdown

    small = http.get(f"/v1/jobs/{job_id}/result/markdown", params={"offset": 2, "limit": 5}, headers=headers).json()
    assert (small["text"], small["offset"], small["next_offset"]) == (markdown[2:7], 2, 7)
    assert small["kind"] == "audio"
    past = http.get(f"/v1/jobs/{job_id}/result/markdown", params={"offset": 10**7}, headers=headers).json()
    assert (past["text"], past["next_offset"]) == ("", None)


def test_markdown_range_limits_and_states(http, fresh_org, org_b, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = _completed_job(http, fresh_org, headers, wav_bytes, "# x")
    pending = new_job(http, headers, wav_bytes)
    not_ready = http.get(f"/v1/jobs/{pending}/result/markdown", headers=headers)
    assert (not_ready.status_code, error_code(not_ready)) == (409, "not_ready")
    too_big = {"limit": jobs_routes.MAX_MARKDOWN_CHUNK_CHARS + 1}
    assert http.get(f"/v1/jobs/{job_id}/result/markdown", params=too_big, headers=headers).status_code == 422
    assert http.get(f"/v1/jobs/{job_id}/result/markdown", params={"offset": -1}, headers=headers).status_code == 422

    other_org = client_headers(org_b)
    assert http.get(f"/v1/jobs/{job_id}/result/markdown", headers=other_org).status_code == 404


# ── Upload validation ────────────────────────────────────────────────────────

def test_oversize_upload_is_413_and_leaves_no_file(http, fresh_org, client_headers, monkeypatch):
    monkeypatch.setattr(jobs_routes, "MAX_UPLOAD_BYTES", 1000)
    headers = client_headers(fresh_org)
    resp = upload(http, headers, b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 5000)
    assert resp.status_code == 413
    assert error_code(resp) == "too_large"
    uploads = storage.org_dir(storage_id(fresh_org)) / "uploads"
    assert not uploads.exists() or list(uploads.iterdir()) == []


def test_chunked_oversize_upload_is_413(http, fresh_org, client_headers, monkeypatch):
    monkeypatch.setattr(jobs_routes, "MAX_UPLOAD_BYTES", 1000)
    boundary = "resona-test-boundary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def body():
        yield head + b"RIFF\x00\x00\x00\x00WAVE"
        for _ in range(10):
            yield b"\x00" * 500
        yield tail

    resp = http.post("/v1/uploads", content=body(), headers={
        **client_headers(fresh_org), "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    assert resp.status_code == 413


def test_empty_and_unsupported_uploads_are_rejected(http, fresh_org, client_headers):
    headers = client_headers(fresh_org)
    empty = upload(http, headers, b"")
    assert empty.status_code == 422 and error_code(empty) == "empty_upload"
    unsupported = upload(http, headers, b"#!/bin/sh\necho hi\n", name="x.sh", ctype="text/plain")
    assert unsupported.status_code == 415 and error_code(unsupported) == "unsupported_media"


# ── Credentials ──────────────────────────────────────────────────────────────

def test_worker_token_is_403_on_client_routes(http, fresh_org, worker_headers, wav_bytes):
    headers = worker_headers(fresh_org)
    assert http.get("/v1/jobs", headers=headers).status_code == 403
    assert upload(http, headers, wav_bytes).status_code == 403


def test_missing_invalid_and_conflicting_credentials_are_401(http, fresh_org, client_headers):
    assert http.get("/v1/jobs").status_code == 401
    assert http.get("/v1/jobs", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401
    bearer = client_headers(fresh_org)["Authorization"]
    other = client_headers(fresh_org)["Authorization"].removeprefix("Bearer ")
    assert http.get("/v1/jobs", headers={"Authorization": bearer, "X-API-Key": other}).status_code == 401


def test_revoked_client_credential_is_401(http, fresh_org, client_headers):
    headers = client_headers(fresh_org)
    token = headers["Authorization"].removeprefix("Bearer ")
    with Session(engine) as session:
        cred = session.exec(select(Credential).where(Credential.secret_hash == hash_token(token))).one()
        revoke_credential(session, cred.id)
        session.commit()
    assert http.get("/v1/jobs", headers=headers).status_code == 401


def test_revoked_worker_credential_is_401(http, fresh_org):
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "pc-revoked", fresh_org)
        revoke_worker(session, worker_id)
    resp = http.post("/v1/workers/claim?wait=0", json={"capabilities": []},
                     headers={"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"})
    assert resp.status_code == 401


def test_hub_app_renders_error_body_at_top_level():
    from resona_api.app import app

    resp = TestClient(app).get("/v1/jobs")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "unauthorized" and isinstance(body["message"], str)


def test_startup_removes_only_legacy_markdown_exports(tmp_path, monkeypatch):
    from resona_api.app import _remove_legacy_markdown_exports

    exports = tmp_path / "md"
    exports.mkdir()
    (exports / "2026-05-01 Mustermann (12).md").write_text("x")
    (exports / "2026-05-01 Mustermann (12).json").write_text("{}")
    (exports / "notes.md").write_text("keep")
    monkeypatch.setenv("MD_PATH", str(exports))

    assert _remove_legacy_markdown_exports() == 2
    assert [p.name for p in exports.iterdir()] == ["notes.md"]
    (exports / "notes.md").unlink()
    assert _remove_legacy_markdown_exports() == 0
    assert not exports.exists()
