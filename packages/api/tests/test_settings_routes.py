"""Settings are revisioned per organization; only org admins can replace them."""


def test_get_settings_returns_revision_and_etag(anon_client, fresh_org, client_headers):
    resp = anon_client.get("/v1/settings", headers=client_headers(fresh_org))

    assert resp.status_code == 200
    body = resp.json()
    assert body["organization_id"] == fresh_org
    assert body["revision"] == 1
    assert body["settings"]["language"] == "de"
    assert resp.headers["ETag"] == '"1"'


def test_get_settings_exposes_no_secrets(anon_client, fresh_org, client_headers):
    resp = anon_client.get("/v1/settings", headers=client_headers(fresh_org))

    text = resp.text.lower()
    assert "token" not in text
    assert "secret" not in text
    assert "api_key" not in text


def test_put_without_if_match_is_428(anon_client, fresh_org, admin_headers):
    resp = anon_client.put(
        "/v1/settings", json={"language": "en"}, headers=admin_headers(fresh_org),
    )

    assert resp.status_code == 428


def test_put_with_stale_if_match_is_409(anon_client, fresh_org, admin_headers, store_settings):
    store_settings(fresh_org, language="fr")  # current revision is now 2

    resp = anon_client.put(
        "/v1/settings",
        json={"language": "en"},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )

    assert resp.status_code == 409


def test_put_creates_new_revision_visible_to_reads(anon_client, fresh_org, admin_headers, client_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"language": "en", "polish": True},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )

    assert resp.status_code == 200
    assert resp.headers["ETag"] == '"2"'
    assert resp.json()["revision"] == 2

    read = anon_client.get("/v1/settings", headers=client_headers(fresh_org))
    assert read.json()["revision"] == 2
    assert read.json()["settings"]["language"] == "en"
    assert read.json()["settings"]["polish"] is True


def test_client_cannot_replace_settings(anon_client, fresh_org, client_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"language": "en"},
        headers={**client_headers(fresh_org), "If-Match": '"1"'},
    )

    assert resp.status_code == 403


def test_invalid_settings_body_is_422(anon_client, fresh_org, admin_headers):
    resp = anon_client.put(
        "/v1/settings",
        json={"no_such_setting": True},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )

    assert resp.status_code == 422


def test_settings_are_per_organization(anon_client, fresh_org, admin_headers, client_headers):
    anon_client.put(
        "/v1/settings",
        json={"language": "en"},
        headers={**admin_headers(fresh_org), "If-Match": '"1"'},
    )
    other = f"{fresh_org}-other"
    from resona_api.admin import create_organization
    from resona_api.db.engine import engine
    from sqlmodel import Session

    with Session(engine) as session:
        create_organization(session, other)

    resp = anon_client.get("/v1/settings", headers=client_headers(other))

    assert resp.json()["organization_id"] == other
    assert resp.json()["revision"] == 1
    assert resp.json()["settings"]["language"] == "de"
