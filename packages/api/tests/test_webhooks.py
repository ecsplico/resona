"""Webhook endpoint registry: URL rules and the org-admin routes under ``/v1/webhooks``."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from resona_api import webhooks
from resona_api.db.engine import engine
from resona_api.db.models import WebhookEndpoint

PUBLIC = "https://hooks.example.org/resona"

_HOSTS = {
    "hooks.example.org": ["93.184.216.34"],
    "other.example.org": ["93.184.216.35"],
    "intranet.example.org": ["10.1.2.3"],
    "mixed.example.org": ["93.184.216.34", "127.0.0.1"],
    "localhost": ["127.0.0.1"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """No test resolves a real name; unknown hosts do not resolve."""
    monkeypatch.delenv("RESONA_WEBHOOK_ALLOW_PRIVATE", raising=False)

    def resolve(host: str) -> list[str]:
        if host not in _HOSTS:
            raise OSError("no such host")
        return _HOSTS[host]

    monkeypatch.setattr(webhooks, "_resolve", resolve)


@pytest.fixture
def api(test_app):
    with TestClient(test_app) as c:
        yield c


def _code(resp) -> str:
    body = resp.json()
    return body.get("error") or body["detail"]["error"]


# ── validate_url ────────────────────────────────────────────────────────

PRIVATE_TARGETS = [
    "https://127.0.0.1/hook",
    "https://localhost/hook",
    "https://[::1]/hook",
    "https://10.0.0.5/hook",
    "https://172.16.4.4/hook",
    "https://192.168.1.10:8443/hook",
    "https://169.254.169.254/latest",
    "https://[fe80::1]/hook",
    "https://0.0.0.0/hook",
    "https://[::ffff:10.0.0.5]/hook",
    "https://intranet.example.org/hook",
    "https://mixed.example.org/hook",
]


def test_validate_url_accepts_a_public_https_url():
    webhooks.validate_url(PUBLIC)
    webhooks.validate_url("https://93.184.216.34:8443/hook?x=1")


@pytest.mark.parametrize("url", PRIVATE_TARGETS)
def test_validate_url_rejects_internal_targets_by_default(url):
    with pytest.raises(webhooks.InvalidWebhookUrl):
        webhooks.validate_url(url)


@pytest.mark.parametrize("url", PRIVATE_TARGETS + ["http://127.0.0.1:8080/hook", "http://intranet.example.org/hook"])
def test_validate_url_accepts_internal_targets_when_the_operator_allows_them(url, monkeypatch):
    monkeypatch.setenv("RESONA_WEBHOOK_ALLOW_PRIVATE", "true")
    webhooks.validate_url(url)


@pytest.mark.parametrize("allow", ["false", "true"])
def test_validate_url_never_accepts_http_to_a_public_host(allow, monkeypatch):
    monkeypatch.setenv("RESONA_WEBHOOK_ALLOW_PRIVATE", allow)
    with pytest.raises(webhooks.InvalidWebhookUrl):
        webhooks.validate_url("http://hooks.example.org/resona")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "hooks.example.org/resona",
        "ftp://hooks.example.org/resona",
        "https://user:pw@hooks.example.org/resona",
        "https://user@hooks.example.org/resona",
        "https:///resona",
        "https://hooks.example.org:99999/resona",
        "https://unknown.example.org/resona",
        "https://hooks.example.org/re sona",
        "https://hooks.example.org/" + "a" * 2048,
    ],
)
def test_validate_url_rejects_malformed_urls(url, monkeypatch):
    monkeypatch.setenv("RESONA_WEBHOOK_ALLOW_PRIVATE", "true")
    with pytest.raises(webhooks.InvalidWebhookUrl):
        webhooks.validate_url(url)


def test_validation_messages_never_echo_the_url():
    with pytest.raises(webhooks.InvalidWebhookUrl) as exc:
        webhooks.validate_url("https://user:hunter2@hooks.example.org/resona")
    assert "hunter2" not in str(exc.value)


# ── routes ──────────────────────────────────────────────────────────────

def test_create_returns_the_secret_once_and_reads_never_contain_it(api, fresh_org, admin_headers):
    headers = admin_headers(fresh_org)
    created = api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert set(body) == {"name", "url", "created_at", "secret"}
    assert (body["name"], body["url"]) == ("app", PUBLIC)
    secret = body["secret"]
    assert secret.startswith("whsec_") and len(base64.b64decode(secret[len("whsec_"):], validate=True)) == 32

    listed = api.get("/v1/webhooks", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == {"items": [{"name": "app", "url": PUBLIC, "created_at": body["created_at"]}]}
    got = api.get("/v1/webhooks/app", headers=headers)
    assert got.status_code == 200 and got.json() == listed.json()["items"][0]
    assert secret not in listed.text and secret not in got.text

    with Session(engine) as session:
        row = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.organization_id == fresh_org)).one()
    assert row.secret == secret and row.revoked_at is None


def test_rotate_returns_a_new_secret_once(api, fresh_org, admin_headers):
    headers = admin_headers(fresh_org)
    first = api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC}, headers=headers).json()["secret"]
    rotated = api.post("/v1/webhooks/app/rotate", headers=headers)
    assert rotated.status_code == 200
    second = rotated.json()["secret"]
    assert second.startswith("whsec_") and second != first
    assert set(rotated.json()) == {"name", "url", "created_at", "secret"}
    with Session(engine) as session:
        row = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.organization_id == fresh_org)).one()
    assert row.secret == second
    assert second not in api.get("/v1/webhooks", headers=headers).text


def test_delete_revokes_and_frees_the_name(api, fresh_org, admin_headers):
    headers = admin_headers(fresh_org)
    api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC}, headers=headers)
    dup = api.post("/v1/webhooks", json={"name": "app", "url": "https://other.example.org/x"}, headers=headers)
    assert (dup.status_code, _code(dup)) == (409, "webhook_exists")

    assert api.delete("/v1/webhooks/app", headers=headers).status_code == 204
    assert api.get("/v1/webhooks", headers=headers).json() == {"items": []}
    for resp in (
        api.get("/v1/webhooks/app", headers=headers),
        api.post("/v1/webhooks/app/rotate", headers=headers),
        api.delete("/v1/webhooks/app", headers=headers),
    ):
        assert (resp.status_code, _code(resp)) == (404, "webhook_not_found")

    again = api.post("/v1/webhooks", json={"name": "app", "url": "https://other.example.org/x"}, headers=headers)
    assert again.status_code == 201
    with Session(engine) as session:
        rows = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.organization_id == fresh_org)).all()
    assert sorted(r.revoked_at is None for r in rows) == [False, True]  # the revoked row is kept


def test_only_org_admins_may_use_the_routes(api, anon_client, fresh_org, admin_headers, client_headers,
                                            worker_headers, operator_headers):
    api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC}, headers=admin_headers(fresh_org))
    for headers in (client_headers(fresh_org), worker_headers(fresh_org), operator_headers(fresh_org)):
        assert api.post("/v1/webhooks", json={"name": "x", "url": PUBLIC}, headers=headers).status_code == 403
        assert api.get("/v1/webhooks", headers=headers).status_code == 403
        assert api.get("/v1/webhooks/app", headers=headers).status_code == 403
        assert api.post("/v1/webhooks/app/rotate", headers=headers).status_code == 403
        assert api.delete("/v1/webhooks/app", headers=headers).status_code == 403
    assert anon_client.get("/v1/webhooks").status_code == 401


def test_another_organizations_endpoint_is_not_found(api, fresh_org, org_b, admin_headers):
    mine = admin_headers(fresh_org)
    theirs = admin_headers(org_b)
    secret = api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC}, headers=mine).json()["secret"]

    assert all(item["name"] != "app" or item["url"] != PUBLIC for item in api.get("/v1/webhooks", headers=theirs).json()["items"])
    for resp in (
        api.get("/v1/webhooks/app", headers=theirs),
        api.post("/v1/webhooks/app/rotate", headers=theirs),
        api.delete("/v1/webhooks/app", headers=theirs),
    ):
        assert (resp.status_code, _code(resp)) == (404, "webhook_not_found")
    with Session(engine) as session:
        row = session.exec(select(WebhookEndpoint).where(WebhookEndpoint.organization_id == fresh_org)).one()
    assert row.secret == secret and row.revoked_at is None


@pytest.mark.parametrize(
    "url",
    ["http://hooks.example.org/x", "https://127.0.0.1/x", "https://10.0.0.5/x", "https://192.168.1.10/x",
     "https://172.16.4.4/x", "https://169.254.169.254/x", "https://user:pw@hooks.example.org/x"],
)
def test_create_rejects_unsafe_urls(api, fresh_org, admin_headers, url):
    headers = admin_headers(fresh_org)
    resp = api.post("/v1/webhooks", json={"name": "app", "url": url}, headers=headers)
    assert (resp.status_code, _code(resp)) == (422, "invalid_webhook_url")
    assert "pw@" not in resp.text
    assert api.get("/v1/webhooks", headers=headers).json() == {"items": []}


@pytest.mark.parametrize("url", ["http://127.0.0.1:8055/x", "https://10.0.0.5/x", "http://192.168.1.10/x",
                                 "https://169.254.169.254/x"])
def test_create_accepts_internal_urls_when_the_operator_allows_them(api, fresh_org, admin_headers, url, monkeypatch):
    monkeypatch.setenv("RESONA_WEBHOOK_ALLOW_PRIVATE", "true")
    resp = api.post("/v1/webhooks", json={"name": "app", "url": url}, headers=admin_headers(fresh_org))
    assert resp.status_code == 201 and resp.json()["url"] == url


@pytest.mark.parametrize("name", ["", "App", "-app", "a_b", "a" * 64, "a b", "ä"])
def test_create_rejects_invalid_names(api, fresh_org, admin_headers, name):
    resp = api.post("/v1/webhooks", json={"name": name, "url": PUBLIC}, headers=admin_headers(fresh_org))
    assert (resp.status_code, _code(resp)) == (422, "invalid_webhook_name")


def test_names_may_use_the_full_pattern(api, fresh_org, admin_headers):
    headers = admin_headers(fresh_org)
    for name in ("a", "0", "a" * 63, "reference-app-2"):
        assert api.post("/v1/webhooks", json={"name": name, "url": PUBLIC}, headers=headers).status_code == 201


def test_create_rejects_unknown_or_missing_fields(api, fresh_org, admin_headers):
    headers = admin_headers(fresh_org)
    assert api.post("/v1/webhooks", json={"name": "app"}, headers=headers).status_code == 422
    extra = api.post("/v1/webhooks", json={"name": "app", "url": PUBLIC, "secret": "x"}, headers=headers)
    assert extra.status_code == 422


def test_routes_are_mounted_on_the_hub_app():
    from resona_api.app import app

    paths = app.openapi()["paths"]
    assert {"/v1/webhooks", "/v1/webhooks/{name}", "/v1/webhooks/{name}/rotate"} <= set(paths)
    listed = paths["/v1/webhooks"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert listed["$ref"].endswith("/WebhookList")
