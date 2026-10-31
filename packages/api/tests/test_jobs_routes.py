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
                       capabilities=[c.model_dump(mode="json") for c in capabilities])
    return worker_id, {"Authorization": f"Bearer {token}"}


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


def test_queue_reason_waiting_capacity_when_capable_worker_online(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    _, worker = make_worker(fresh_org)
    beat = http.post("/v1/workers/heartbeat", headers=worker, json={
        "worker_protocol": 1, "runtime_version": "0.1.0", "reported_capabilities": [], "ready": True,
    })
    assert beat.status_code == 200
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).json()["queue_reason"] == "waiting_capacity"


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


def test_delete_requires_terminal_state_then_removes_everything(http, fresh_org, client_headers, wav_bytes):
    headers = client_headers(fresh_org)
    job_id = new_job(http, headers, wav_bytes)
    resp = http.delete(f"/v1/jobs/{job_id}", headers=headers)
    assert resp.status_code == 409
    assert error_code(resp) == "job_not_terminal"

    http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    assert http.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 204
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).status_code == 404
    assert not storage.job_dir(storage_id(fresh_org), job_id).exists()


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
                     headers={"Authorization": f"Bearer {token}"})
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
