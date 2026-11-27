"""Job ``context``: the text around the insertion point lives in the postprocess artifact only."""

import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Candidate, Endpoint
from resona_job_core.features import CONTEXT, known_features
from resona_api import storage
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import Job, Organization
from resona_api.releases import hub_release_id
from release_approval import approve_release

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")

LOCAL_LLM = Endpoint(id="local-llm", kind="local_llm", model="qwen-local", url="http://127.0.0.1:9/v1")
BEFORE = "Im Befund von Erika Mustermann steht:"
CONTEXT_BODY = {"before": BEFORE, "after": "Weiter im Text."}


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def upload(http, headers, data):
    return http.post("/v1/uploads", files={"file": ("a.wav", data, "audio/wav")}, headers=headers)


def submit(http, headers, body, *, key=None):
    return http.post("/v1/jobs", json=body, headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex})


def make_worker(org_id):
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "context-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[MAC_AUDIO.model_dump(mode="json")]).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return worker_id, {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def claim(http, headers):
    return http.post("/v1/workers/claim?wait=0",
                     json={"capabilities": [MAC_AUDIO.model_dump(mode="json")]}, headers=headers)


def job_row(job_id) -> Job:
    with Session(engine) as session:
        return session.get(Job, job_id)


def storage_id(org_id) -> str:
    with Session(engine) as session:
        return session.get(Organization, org_id).storage_id


@pytest.fixture
def cleanup_org(fresh_org, store_settings, store_profile):
    store_settings(fresh_org, endpoints=[LOCAL_LLM.model_dump(mode="json")])
    store_profile(fresh_org, "cleanup", {"name": "cleanup", "steps": [
        {"type": "llm", "name": "cleanup", "prompt": "Clean up.", "endpoint": "local-llm"},
    ]})
    store_profile(fresh_org, "rules", {"name": "rules", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
    ]})
    return fresh_org


def _submit(http, headers, wav_bytes, *, key=None, **fields):
    upload_id = upload(http, headers, wav_bytes).json()["upload_id"]
    return submit(http, headers, {"kind": "audio", "upload_id": upload_id, **fields}, key=key)


def test_context_lives_in_the_artifact_and_the_snapshot_only_flags_it(http, cleanup_org, client_headers, wav_bytes):
    resp = _submit(http, client_headers(cleanup_org), wav_bytes, profile="cleanup", context=CONTEXT_BODY)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    stored = job_row(job_id).execution_snapshot
    assert BEFORE not in stored
    assert json.loads(stored)["postprocess"]["context"] is True
    document = storage.postprocess_path(storage_id(cleanup_org), job_id).read_bytes()
    assert json.loads(document)["context"] == CONTEXT_BODY
    assert hashlib.sha256(document).hexdigest() == json.loads(stored)["postprocess"]["sha256"]


@pytest.mark.parametrize("profile", [None, "rules"])
def test_context_without_an_llm_step_is_422(http, cleanup_org, client_headers, wav_bytes, profile):
    headers = client_headers(cleanup_org)
    fields = {"context": CONTEXT_BODY, **({"profile": profile} if profile else {})}
    resp = _submit(http, headers, wav_bytes, **fields)
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_context"
    assert http.get("/v1/jobs", headers=headers).json()["items"] == []


@pytest.mark.parametrize("context", [{}, {"before": " "}, {"before": "x" * 4001}, {"cursor": "x"}])
def test_malformed_context_is_rejected(http, cleanup_org, client_headers, wav_bytes, context):
    resp = _submit(http, client_headers(cleanup_org), wav_bytes, profile="cleanup", context=context)
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_request"


def test_context_is_part_of_the_idempotency_hash(http, cleanup_org, client_headers, wav_bytes):
    headers = client_headers(cleanup_org)
    key = uuid.uuid4().hex
    first = _submit(http, headers, wav_bytes, key=key, profile="cleanup", context=CONTEXT_BODY)
    assert first.status_code == 202
    again = _submit(http, headers, wav_bytes, key=key, profile="cleanup", context=CONTEXT_BODY)
    assert again.json()["job_id"] == first.json()["job_id"]
    changed = _submit(http, headers, wav_bytes, key=key, profile="cleanup", context={"before": "anders"})
    assert changed.status_code == 409
    assert error_code(changed) == "idempotency_conflict"


def _beat(http, worker, features):
    resp = http.post("/v1/workers/heartbeat", headers=worker, json={
        "worker_protocol": 1, "runtime_version": hub_release_id(), "ready": True,
        "reported_capabilities": [MAC_AUDIO.model_dump(mode="json")], "features": sorted(features),
    })
    assert resp.status_code == 200, resp.text


def test_only_a_worker_that_knows_context_claims_the_job(http, cleanup_org, client_headers, wav_bytes):
    headers = client_headers(cleanup_org)
    job_id = _submit(http, headers, wav_bytes, profile="cleanup", context=CONTEXT_BODY).json()["job_id"]
    _, worker = make_worker(cleanup_org)

    _beat(http, worker, known_features() - {CONTEXT})
    assert http.get(f"/v1/jobs/{job_id}", headers=headers).json()["queue_reason"] == "worker_outdated"
    assert claim(http, worker).status_code == 204

    _beat(http, worker, known_features())
    assert claim(http, worker).json()["job_id"] == job_id
