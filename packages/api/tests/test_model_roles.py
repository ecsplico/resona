"""Model roles: ``"endpoint": "@<role>"`` resolves at admission to the organization's endpoint id."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Endpoint
from resona_api import storage
from resona_api.db.engine import engine
from resona_api.db.models import Job, Organization

LOCAL_LLM = Endpoint(id="local-llm", kind="local_llm", model="qwen-local", url="http://127.0.0.1:9/v1")
BIG_LLM = Endpoint(id="big-llm", kind="local_llm", model="qwen-big", url="http://127.0.0.1:10/v1")


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def roles_org(fresh_org, store_settings, store_profile):
    store_settings(
        fresh_org,
        endpoints=[LOCAL_LLM.model_dump(mode="json"), BIG_LLM.model_dump(mode="json")],
        model_roles={"polish": "big-llm"},
    )
    store_profile(fresh_org, "polished", {"name": "polished", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
        {"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "@polish"},
    ]})
    store_profile(fresh_org, "titled", {"name": "titled", "steps": [
        {"type": "extract", "name": "title", "prompt": "Title as JSON.", "endpoint": "@title"},
    ]})
    return fresh_org


def _submit(http, headers, wav_bytes, **fields):
    upload_id = http.post(
        "/v1/uploads", files={"file": ("a.wav", wav_bytes, "audio/wav")}, headers=headers,
    ).json()["upload_id"]
    return http.post(
        "/v1/jobs", json={"kind": "audio", "upload_id": upload_id, **fields},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )


def _document(org_id: str, job_id: int) -> dict:
    with Session(engine) as session:
        storage_id = session.get(Organization, org_id).storage_id
    return json.loads(storage.postprocess_path(storage_id, job_id).read_bytes())


def test_admission_pins_the_roles_endpoint_id(http, roles_org, client_headers, wav_bytes):
    resp = _submit(http, client_headers(roles_org), wav_bytes, profile="polished")
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    document = _document(roles_org, job_id)
    assert [step.get("endpoint") for step in document["steps"]] == [None, "big-llm"]
    with Session(engine) as session:
        snapshot = json.loads(session.get(Job, job_id).execution_snapshot)
    assert "@polish" not in json.dumps(snapshot)


def test_unassigned_role_is_422_and_creates_no_job(http, roles_org, client_headers, wav_bytes):
    headers = client_headers(roles_org)
    resp = _submit(http, headers, wav_bytes, profile="titled")
    assert resp.status_code == 422
    body = resp.json()
    body = body.get("detail", body)
    assert body["error"] == "role_unassigned"
    assert "title" in body["message"]
    assert http.get("/v1/jobs", headers=headers).json()["items"] == []


def test_reassigning_a_role_changes_new_jobs_only(http, roles_org, client_headers, store_settings, wav_bytes):
    headers = client_headers(roles_org)
    first = _submit(http, headers, wav_bytes, profile="polished").json()["job_id"]
    store_settings(roles_org, model_roles={"polish": "local-llm"})
    second = _submit(http, headers, wav_bytes, profile="polished").json()["job_id"]

    assert _document(roles_org, first)["steps"][1]["endpoint"] == "big-llm"
    assert _document(roles_org, second)["steps"][1]["endpoint"] == "local-llm"


def test_roles_of_an_included_block_resolve(http, roles_org, client_headers, store_profile, wav_bytes):
    store_profile(roles_org, "polish-block", {"name": "polish-block", "block": True, "steps": [
        {"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "@polish"},
    ]})
    store_profile(roles_org, "composed", {"name": "composed", "steps": [
        {"type": "include", "profile": "polish-block"},
    ]})
    resp = _submit(http, client_headers(roles_org), wav_bytes, profile="composed")
    assert resp.status_code == 202, resp.text
    assert _document(roles_org, resp.json()["job_id"])["steps"] == [
        {"type": "llm", "name": "polish", "prompt": "Polish.", "endpoint": "big-llm"},
    ]


def test_settings_put_rejects_a_role_on_an_unknown_endpoint(anon_client, fresh_org, admin_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"model_roles": {"polish": "nowhere"}},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )
    assert resp.status_code == 422


def test_settings_put_rejects_a_bad_role_name(anon_client, fresh_org, admin_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"endpoints": [LOCAL_LLM.model_dump(mode="json")], "model_roles": {"Polish": "local-llm"}},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )
    assert resp.status_code == 422


def test_settings_round_trip_model_roles(anon_client, fresh_org, admin_headers, client_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"endpoints": [LOCAL_LLM.model_dump(mode="json")], "model_roles": {"cleanup": "local-llm"}},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )
    assert resp.status_code == 200, resp.text
    read = anon_client.get("/v1/settings", headers=client_headers(fresh_org)).json()
    assert read["settings"]["model_roles"] == {"cleanup": "local-llm"}
