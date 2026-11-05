"""Hub management API: org admins manage their own organization, hub operators every one."""

import json
import logging
import uuid

import pytest
from sqlmodel import Session

from resona_job_core import Candidate
from resona_api import releases
from resona_api.admin import enroll_worker, issue_credential
from resona_api.db.engine import engine
from resona_api.db.models import Credential

CAND = Candidate(
    engine="mlx-whisper",
    model="mlx-community/whisper-large-v3-mlx",
    model_revision="rev-1",
    accelerator="metal",
)
CORPUS = "c" * 64


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


@pytest.fixture
def operator(operator_headers, org_a):
    return operator_headers(org_a)


def test_me_reports_role_and_home_organization(anon_client, admin_headers, operator, org_a):
    admin = anon_client.get("/v1/admin/me", headers=admin_headers(org_a))
    op = anon_client.get("/v1/admin/me", headers=operator)

    assert admin.status_code == 200
    assert admin.json()["role"] == "org_admin" and admin.json()["organization_id"] == org_a
    assert op.status_code == 200
    assert op.json()["role"] == "hub_operator" and op.json()["organization_id"] == org_a


def test_org_admin_lists_only_its_own_organization(anon_client, admin_headers, org_a, org_b):
    resp = anon_client.get("/v1/admin/organizations", headers=admin_headers(org_a))

    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["items"]] == [org_a]


def test_org_admin_cannot_create_organizations(anon_client, admin_headers, org_a):
    resp = anon_client.post(
        "/v1/admin/organizations", json={"id": f"org-{uuid.uuid4().hex[:12]}"}, headers=admin_headers(org_a)
    )

    assert resp.status_code == 403
    assert error_code(resp) == "forbidden"


def test_operator_lists_every_organization(anon_client, operator, org_a, org_b):
    resp = anon_client.get("/v1/admin/organizations", headers=operator)

    assert resp.status_code == 200
    ids = [item["id"] for item in resp.json()["items"]]
    assert org_a in ids and org_b in ids
    assert ids == sorted(ids)


def test_operator_creates_organization_once(anon_client, operator):
    org_id = f"org-{uuid.uuid4().hex[:12]}"

    created = anon_client.post("/v1/admin/organizations", json={"id": org_id}, headers=operator)
    duplicate = anon_client.post("/v1/admin/organizations", json={"id": org_id}, headers=operator)
    listed = anon_client.get("/v1/admin/organizations", headers=operator)

    assert created.status_code == 201
    assert created.json()["id"] == org_id and created.json()["created_at"]
    assert duplicate.status_code == 409
    assert error_code(duplicate) == "conflict"
    assert org_id in [item["id"] for item in listed.json()["items"]]


def test_operator_has_no_content_access(anon_client, operator):
    jobs = anon_client.get("/v1/jobs", headers=operator)
    settings = anon_client.put(
        "/v1/settings", json={"language": "en"}, headers={**operator, "If-Match": '"1"'}
    )

    assert jobs.status_code == 403
    assert settings.status_code == 403


def test_client_token_is_forbidden(anon_client, client_headers, org_a):
    resp = anon_client.get("/v1/admin/me", headers=client_headers(org_a))

    assert resp.status_code == 403
    assert error_code(resp) == "forbidden"


def test_missing_token_is_unauthorized(anon_client):
    assert anon_client.get("/v1/admin/me").status_code == 401
    assert anon_client.get("/v1/admin/organizations").status_code == 401


def test_admin_page_is_served_with_strict_headers(anon_client):
    resp = anon_client.get("/admin")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("asset", ["admin.js", "admin.css"])
def test_admin_assets_are_served(anon_client, asset):
    resp = anon_client.get(f"/admin/{asset}")

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"


def test_unknown_admin_asset_is_not_found(anon_client):
    assert anon_client.get("/admin/evil.js").status_code == 404


# ── Organization-scoped endpoints ────────────────────────────────────────────

def org_path(org_id: str, suffix: str) -> str:
    return f"/v1/admin/organizations/{org_id}/{suffix}"


def enroll(org_id: str, name: str = "admin-worker") -> tuple[str, dict[str, str]]:
    """Enroll an unapproved worker; returns its id and worker headers."""
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, name, org_id)
        session.commit()
    return worker_id, {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def heartbeat(http, headers, caps=(CAND,)):
    return http.post("/v1/workers/heartbeat", json={
        "worker_protocol": 1,
        "runtime_version": "0.1.0",
        "reported_capabilities": [c.model_dump(mode="json") for c in caps],
    }, headers=headers)


def worker_item(http, headers, org_id: str, worker_id: str) -> dict:
    resp = http.get(org_path(org_id, "workers"), headers=headers)
    assert resp.status_code == 200
    [item] = [w for w in resp.json()["items"] if w["id"] == worker_id]
    return item


def foreign_credential(org_id: str) -> str:
    with Session(engine) as session:
        cred_id, _ = issue_credential(session, org_id, "client")
        session.commit()
    return cred_id


def submit_job(http, headers, data: bytes, name: str) -> int:
    upload = http.post("/v1/uploads", files={"file": (name, data, "audio/wav")}, headers=headers)
    assert upload.status_code == 201, upload.text
    resp = http.post("/v1/jobs", json={"kind": "audio", "upload_id": upload.json()["upload_id"]},
                     headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


# Workers

def test_org_admin_cannot_see_another_organizations_workers(anon_client, admin_headers, org_a, org_b):
    resp = anon_client.get(org_path(org_b, "workers"), headers=admin_headers(org_a))

    assert resp.status_code == 404
    assert error_code(resp) == "organization_not_found"


def test_operator_lists_workers_of_every_organization(anon_client, operator, org_a, org_b):
    for org_id in (org_a, org_b):
        resp = anon_client.get(org_path(org_id, "workers"), headers=operator)
        assert resp.status_code == 200
        assert isinstance(resp.json()["items"], list)

    unknown = anon_client.get(org_path(f"org-{uuid.uuid4().hex[:12]}", "workers"), headers=operator)
    assert unknown.status_code == 404
    assert error_code(unknown) == "organization_not_found"


@pytest.mark.parametrize("private", [True, False])
def test_approve_uses_the_workers_reported_capabilities(anon_client, admin_headers, fresh_org, private):
    admin = admin_headers(fresh_org)
    worker_id, worker = enroll(fresh_org)
    assert heartbeat(anon_client, worker).status_code == 200

    before = worker_item(anon_client, admin, fresh_org, worker_id)
    assert before["online"] is True and before["last_seen"]
    assert before["approved"] is False and before["revoked"] is False
    assert before["runtime_version"] == "0.1.0"
    assert before["reported_capabilities"] == [CAND.model_dump(mode="json")]

    resp = anon_client.post(org_path(fresh_org, f"workers/{worker_id}/approve"),
                            json={"private": private}, headers=admin)

    assert resp.status_code == 200
    item = resp.json()
    assert item["id"] == worker_id
    assert item["approved"] is True and item["approved_private"] is private
    assert item["approved_capabilities"] == item["reported_capabilities"] == [CAND.model_dump(mode="json")]
    beat = heartbeat(anon_client, worker).json()
    assert beat["approved"] is True and beat["approved_private"] is private


def test_worker_without_reported_capabilities_is_not_approved(anon_client, admin_headers, fresh_org):
    admin = admin_headers(fresh_org)
    silent_id, _ = enroll(fresh_org, "silent-worker")
    empty_id, empty = enroll(fresh_org, "empty-worker")
    assert heartbeat(anon_client, empty, caps=()).status_code == 200

    for worker_id in (silent_id, empty_id):
        resp = anon_client.post(org_path(fresh_org, f"workers/{worker_id}/approve"),
                                json={"private": True}, headers=admin)
        assert resp.status_code == 409
        assert error_code(resp) == "no_reported_capabilities"
        assert worker_item(anon_client, admin, fresh_org, worker_id)["approved"] is False


def test_revoked_worker_loses_access_and_cannot_be_approved(anon_client, admin_headers, fresh_org):
    admin = admin_headers(fresh_org)
    worker_id, worker = enroll(fresh_org)
    assert heartbeat(anon_client, worker).status_code == 200

    revoked = anon_client.post(org_path(fresh_org, f"workers/{worker_id}/revoke"), headers=admin)
    again = anon_client.post(org_path(fresh_org, f"workers/{worker_id}/revoke"), headers=admin)

    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True and revoked.json()["online"] is False
    assert again.status_code == 200 and again.json()["revoked"] is True
    assert heartbeat(anon_client, worker).status_code == 401
    approve = anon_client.post(org_path(fresh_org, f"workers/{worker_id}/approve"),
                               json={"private": True}, headers=admin)
    assert approve.status_code == 409


def test_workers_are_listed_active_first_then_by_name(anon_client, admin_headers, fresh_org):
    admin = admin_headers(fresh_org)
    enroll(fresh_org, "zeta")
    alpha_id, _ = enroll(fresh_org, "alpha")
    enroll(fresh_org, "beta")
    assert anon_client.post(org_path(fresh_org, f"workers/{alpha_id}/revoke"), headers=admin).status_code == 200

    resp = anon_client.get(org_path(fresh_org, "workers"), headers=admin)

    assert [item["name"] for item in resp.json()["items"]] == ["beta", "zeta", "alpha"]


def test_worker_of_another_organization_is_not_found(anon_client, admin_headers, operator, fresh_org, org_b):
    foreign_id, foreign = enroll(org_b, "foreign-worker")
    assert heartbeat(anon_client, foreign).status_code == 200

    for headers in (admin_headers(fresh_org), operator):
        approve = anon_client.post(org_path(fresh_org, f"workers/{foreign_id}/approve"),
                                   json={"private": True}, headers=headers)
        revoke = anon_client.post(org_path(fresh_org, f"workers/{foreign_id}/revoke"), headers=headers)
        assert (approve.status_code, error_code(approve)) == (404, "worker_not_found")
        assert (revoke.status_code, error_code(revoke)) == (404, "worker_not_found")

    unknown = anon_client.post(org_path(fresh_org, "workers/wrk_missing/revoke"), headers=operator)
    assert (unknown.status_code, error_code(unknown)) == (404, "worker_not_found")
    item = worker_item(anon_client, operator, org_b, foreign_id)
    assert item["approved"] is False and item["revoked"] is False


# Credentials

def test_issued_client_token_works_until_revoked(anon_client, admin_headers, fresh_org, caplog):
    caplog.set_level(logging.INFO)
    admin = admin_headers(fresh_org)

    issued = anon_client.post(org_path(fresh_org, "credentials"), json={"role": "client"}, headers=admin)

    assert issued.status_code == 201
    cred_id, token = issued.json()["id"], issued.json()["token"]
    assert issued.json()["role"] == "client" and token
    client = {"Authorization": f"Bearer {token}"}
    assert anon_client.get("/v1/jobs", headers=client).status_code == 200

    listed = anon_client.get(org_path(fresh_org, "credentials"), headers=admin)
    assert listed.status_code == 200
    with Session(engine) as session:
        secret_hash = session.get(Credential, cred_id).secret_hash
    assert token not in listed.text and secret_hash not in listed.text
    assert all("secret_hash" not in item and "token" not in item for item in listed.json()["items"])
    [item] = [c for c in listed.json()["items"] if c["id"] == cred_id]
    assert item["role"] == "client" and item["worker_id"] is None
    assert item["created_at"] and item["revoked_at"] is None
    assert token not in caplog.text

    revoked = anon_client.post(org_path(fresh_org, f"credentials/{cred_id}/revoke"), headers=admin)

    assert revoked.status_code == 200
    assert revoked.json()["id"] == cred_id and revoked.json()["revoked_at"]
    assert anon_client.get("/v1/jobs", headers=client).status_code == 401


def test_issued_admin_token_manages_the_organization(anon_client, operator, fresh_org):
    issued = anon_client.post(org_path(fresh_org, "credentials"), json={"role": "org_admin"}, headers=operator)

    assert issued.status_code == 201
    me = anon_client.get("/v1/admin/me", headers={"Authorization": f"Bearer {issued.json()['token']}"})
    assert me.status_code == 200
    assert (me.json()["role"], me.json()["organization_id"]) == ("org_admin", fresh_org)


def test_own_credential_cannot_be_revoked(anon_client, admin_headers, fresh_org):
    admin = admin_headers(fresh_org)
    own_id = anon_client.get("/v1/admin/me", headers=admin).json()["credential_id"]

    resp = anon_client.post(org_path(fresh_org, f"credentials/{own_id}/revoke"), headers=admin)

    assert resp.status_code == 409
    assert error_code(resp) == "cannot_revoke_self"
    assert anon_client.get("/v1/admin/me", headers=admin).status_code == 200


def test_hub_operator_credentials_cannot_be_issued_over_http(anon_client, admin_headers, operator, fresh_org):
    for headers in (admin_headers(fresh_org), operator):
        resp = anon_client.post(org_path(fresh_org, "credentials"), json={"role": "hub_operator"}, headers=headers)
        assert resp.status_code == 422


def test_worker_credential_is_revoked_through_the_worker(anon_client, admin_headers, fresh_org):
    admin = admin_headers(fresh_org)
    worker_id, worker = enroll(fresh_org)
    listed = anon_client.get(org_path(fresh_org, "credentials"), headers=admin).json()["items"]
    [cred] = [c for c in listed if c["worker_id"] == worker_id]
    assert cred["role"] == "worker"

    resp = anon_client.post(org_path(fresh_org, f"credentials/{cred['id']}/revoke"), headers=admin)

    assert resp.status_code == 409
    assert error_code(resp) == "use_worker_revoke"
    assert heartbeat(anon_client, worker).status_code == 200


def test_credential_of_another_organization_is_not_found(anon_client, admin_headers, operator, fresh_org, org_b):
    foreign_id = foreign_credential(org_b)

    for headers in (admin_headers(fresh_org), operator):
        resp = anon_client.post(org_path(fresh_org, f"credentials/{foreign_id}/revoke"), headers=headers)
        assert (resp.status_code, error_code(resp)) == (404, "credential_not_found")

    unknown = anon_client.post(org_path(fresh_org, "credentials/cred_missing/revoke"), headers=operator)
    assert (unknown.status_code, error_code(unknown)) == (404, "credential_not_found")
    with Session(engine) as session:
        assert session.get(Credential, foreign_id).revoked_at is None


def test_org_admin_cannot_issue_credentials_for_another_organization(anon_client, admin_headers, org_a, org_b):
    resp = anon_client.post(org_path(org_b, "credentials"), json={"role": "client"}, headers=admin_headers(org_a))

    assert resp.status_code == 404
    assert error_code(resp) == "organization_not_found"


# Jobs

def test_operator_pages_job_metadata_of_any_organization(
    anon_client, operator, client_headers, fresh_org, wav_bytes
):
    client = client_headers(fresh_org)
    older = submit_job(anon_client, client, wav_bytes, "secret-interview.wav")
    newer = submit_job(anon_client, client, wav_bytes, "secret-interview.wav")

    first = anon_client.get(org_path(fresh_org, "jobs") + "?limit=1", headers=operator)

    assert first.status_code == 200
    assert [item["job_id"] for item in first.json()["items"]] == [newer]
    assert first.json()["items"][0]["status"] == "pending"
    cursor = first.json()["next_cursor"]
    assert cursor
    second = anon_client.get(org_path(fresh_org, "jobs"), params={"limit": 1, "cursor": cursor}, headers=operator)
    assert [item["job_id"] for item in second.json()["items"]] == [older]
    assert second.json()["next_cursor"] is None
    assert "secret-interview" not in first.text + second.text


def test_org_admin_cannot_list_another_organizations_jobs(anon_client, admin_headers, org_a, org_b):
    resp = anon_client.get(org_path(org_b, "jobs"), headers=admin_headers(org_a))

    assert resp.status_code == 404
    assert error_code(resp) == "organization_not_found"


# Settings & baselines

def test_settings_show_the_current_revision(anon_client, admin_headers, fresh_org, store_settings):
    revision = store_settings(fresh_org, language="fr")

    resp = anon_client.get(org_path(fresh_org, "settings"), headers=admin_headers(fresh_org))

    assert resp.status_code == 200
    assert resp.json()["revision"] == revision
    assert isinstance(resp.json()["settings"], dict)
    assert resp.json()["settings"]["language"] == "fr"


def evaluation_body(release_id: str) -> dict:
    """A passing cpu-private evaluation summary with its declared fixture checks."""
    return {
        "profile": "cpu-private", "execution_profile": "cpu-private", "platform": "cpu",
        "release_id": release_id, "corpus_sha256": CORPUS, "baseline_revision": None,
        "fixtures": {
            "de-short": {"wer": 0.10, "cer": 0.04},
            "silence": {"silence_insertions": 0},
            "table": {"cer": 0.01, "missing_cells": 0, "order_failures": 0},
        },
        "fixture_checks": {
            "de-short": {"kind": "audio", "checks": ["wer", "cer"]},
            "silence": {"kind": "audio", "checks": ["silence"]},
            "table": {"kind": "document", "checks": ["cer", "table_cells", "reading_order"]},
        },
        "gate": {"passed": True, "failures": []},
    }


def promote_new_evaluation(http, headers, release_id: str, expected: int | None) -> int:
    recorded = http.post("/v1/evaluations", json=evaluation_body(release_id), headers=headers)
    assert recorded.status_code == 201, recorded.text
    evaluation_id = recorded.json()["evaluation_id"]
    promoted = http.post(f"/v1/evaluations/{evaluation_id}/promote",
                         json={"expected_baseline_revision": expected}, headers=headers)
    assert promoted.status_code == 200, promoted.text
    return evaluation_id


def test_baselines_show_the_highest_revision_and_approved_releases(
    anon_client, admin_headers, operator, fresh_org
):
    release_id = f"9.2.0+e{uuid.uuid4().hex[:10]}"
    manifest = {
        "release_id": release_id, "created_at": "2026-11-01T10:00:00Z",
        "wheels": [{"name": "resona-worker", "version": release_id, "sha256": "b" * 64}],
        "images": [], "models": [], "worker_protocol": 1,
    }
    with Session(engine) as session:
        releases.stage_release(session, json.dumps(manifest).encode())
    admin = admin_headers(fresh_org)

    promote_new_evaluation(anon_client, admin, release_id, None)
    latest = promote_new_evaluation(anon_client, admin, release_id, 1)
    resp = anon_client.get(org_path(fresh_org, "baselines"), headers=operator)

    assert resp.status_code == 200
    [baseline] = resp.json()["items"]
    assert baseline["created_at"]
    assert {k: baseline[k] for k in ("execution_profile", "platform", "revision", "evaluation_id",
                                     "release_id", "corpus_sha256")} == {
        "execution_profile": "cpu-private", "platform": "cpu", "revision": 2,
        "evaluation_id": latest, "release_id": release_id, "corpus_sha256": CORPUS,
    }
    [approval] = resp.json()["approved_releases"]
    assert (approval["execution_profile"], approval["platform"], approval["release_id"]) == (
        "cpu-private", "cpu", release_id
    )
    assert approval["evaluation_id"] == latest and approval["approved_at"]


def test_organization_without_baselines_lists_none(anon_client, admin_headers, fresh_org):
    resp = anon_client.get(org_path(fresh_org, "baselines"), headers=admin_headers(fresh_org))

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "approved_releases": []}
