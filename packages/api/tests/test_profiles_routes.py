"""Organization-scoped profile routes: role gates, revisions, isolation, bundled default."""

import pytest

BODY = {"name": "arzt", "description": "AB", "steps": []}


def _put(client, headers, name, body):
    return client.put(f"/profiles/{name}", json=body, headers=headers)


def test_client_lists_and_reads_profile_written_by_admin(
        anon_client, fresh_org, admin_headers, client_headers):
    assert _put(anon_client, admin_headers(fresh_org), "arzt", BODY).status_code == 200

    listing = anon_client.get("/profiles", headers=client_headers(fresh_org)).json()
    assert {"name": "arzt", "revision": 1} in listing["profiles"]

    resp = anon_client.get("/profiles/arzt", headers=client_headers(fresh_org))
    assert resp.status_code == 200
    assert resp.json()["profile"]["description"] == "AB"


def test_client_cannot_write_profiles(anon_client, fresh_org, client_headers, admin_headers):
    headers = client_headers(fresh_org)
    assert _put(anon_client, headers, "arzt", BODY).status_code == 403
    assert anon_client.delete("/profiles/arzt", headers=headers).status_code == 403


def test_worker_credential_cannot_read_or_write_profiles(
        anon_client, fresh_org, worker_headers, admin_headers):
    headers = worker_headers(fresh_org)
    assert anon_client.get("/profiles", headers=headers).status_code == 403
    assert _put(anon_client, headers, "arzt", BODY).status_code == 403


def test_admin_put_creates_revision_one_then_two_and_latest_is_returned(
        anon_client, fresh_org, admin_headers, client_headers):
    admin = admin_headers(fresh_org)
    first = _put(anon_client, admin, "arzt", BODY)
    assert first.json() == {"name": "arzt", "revision": 1}

    second = _put(anon_client, admin, "arzt", {**BODY, "description": "two"})
    assert second.json() == {"name": "arzt", "revision": 2}

    resp = anon_client.get("/profiles/arzt", headers=client_headers(fresh_org)).json()
    assert resp["revision"] == 2
    assert resp["profile"]["description"] == "two"


@pytest.mark.parametrize("name", ["..x", "A%20B", "a" * 65])
def test_invalid_profile_name_is_422(anon_client, fresh_org, admin_headers, name):
    resp = _put(anon_client, admin_headers(fresh_org), name, BODY)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_profile_name"


def test_name_of_maximum_length_is_accepted(anon_client, fresh_org, admin_headers):
    assert _put(anon_client, admin_headers(fresh_org), "a" * 64, BODY).status_code == 200


@pytest.mark.parametrize("body", [
    {"name": "bad", "steps": [{"type": "magic"}]},
    {"name": "bad", "steps": [{"type": "llm", "source": "/etc/passwd"}]},
])
def test_invalid_profile_body_is_422(anon_client, fresh_org, admin_headers, body):
    resp = _put(anon_client, admin_headers(fresh_org), "bad", body)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_profile"


def test_other_organization_cannot_see_profile(
        anon_client, org_a, org_b, admin_headers, client_headers):
    name = "isolated-a"
    assert _put(anon_client, admin_headers(org_a), name, BODY).status_code == 200

    assert anon_client.get(f"/profiles/{name}",
                           headers=client_headers(org_b)).status_code == 404
    listing = anon_client.get("/profiles", headers=client_headers(org_b)).json()
    assert name not in {row["name"] for row in listing["profiles"]}


def test_bundled_default_is_readable_but_not_deletable_or_overwritable(
        anon_client, fresh_org, admin_headers, client_headers):
    headers = client_headers(fresh_org)
    assert anon_client.get("/profiles/default", headers=headers).status_code == 200

    assert anon_client.delete("/profiles/default",
                              headers=admin_headers(fresh_org)).status_code == 404

    overwrite = _put(anon_client, admin_headers(fresh_org), "default", BODY)
    assert overwrite.status_code == 409
    assert overwrite.json()["detail"]["error"] == "reserved_profile_name"
    assert anon_client.get("/profiles/default", headers=headers).json()["revision"] == 0


def test_delete_tombstones_profile_then_get_is_404(
        anon_client, fresh_org, admin_headers, client_headers):
    admin = admin_headers(fresh_org)
    assert _put(anon_client, admin, "arzt", BODY).status_code == 200

    assert anon_client.delete("/profiles/arzt", headers=admin).status_code == 200
    assert anon_client.get("/profiles/arzt",
                           headers=client_headers(fresh_org)).status_code == 404
    listing = anon_client.get("/profiles", headers=client_headers(fresh_org)).json()
    assert "arzt" not in {row["name"] for row in listing["profiles"]}
