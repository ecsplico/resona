"""Hub management API: org admins manage their own organization, hub operators every one."""

import uuid

import pytest


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
