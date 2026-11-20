"""Webhook client methods (respx) and the pure signature verifier."""
import base64
import json
import secrets
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from resona_client.client import (
    ResonaAPIError,
    ResonaClient,
    Webhook,
    WebhookCreated,
    WebhookDelivery,
)
from resona_client.webhooks import InvalidWebhookSignature, verify
from resona_job_core.webhooks import SECRET_PREFIX, JobEvent, sign

BASE = "http://localhost:7000"
UPLOAD_JSON = {"upload_id": "upl_1", "sha256": "ab" * 32, "size": 10}
CREATED = {
    "name": "backend",
    "url": "https://hooks.example/resona",
    "created_at": "2026-11-20T10:00:00Z",
    "secret": "whsec_c2VjcmV0LWJ5dGVz",
}


@pytest.fixture
def client():
    c = ResonaClient(base_url=BASE, api_key="admin-key")
    yield c
    c.close()


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<160h", *([0] * 160)))
    return path


# ── Client methods ────────────────────────────────────────────────────────────

@respx.mock
def test_create_webhook_posts_name_and_url_and_returns_secret(client):
    route = respx.post(f"{BASE}/v1/webhooks").mock(return_value=httpx.Response(201, json=CREATED))
    created = client.create_webhook("backend", "https://hooks.example/resona")
    assert json.loads(route.calls[0].request.content) == {
        "name": "backend", "url": "https://hooks.example/resona",
    }
    assert route.calls[0].request.headers["Authorization"] == "Bearer admin-key"
    assert created == WebhookCreated(**CREATED)
    assert created.secret == CREATED["secret"]
    assert CREATED["secret"] not in repr(created)


@pytest.mark.parametrize("code", ["invalid_webhook_url", "invalid_webhook_name"])
@respx.mock
def test_create_webhook_maps_422_codes(client, code):
    respx.post(f"{BASE}/v1/webhooks").mock(
        return_value=httpx.Response(422, json={"error": code, "message": "rejected"}))
    with pytest.raises(ResonaAPIError) as exc:
        client.create_webhook("backend", "http://10.0.0.1/hook")
    assert (exc.value.status, exc.value.code) == (422, code)


@respx.mock
def test_create_webhook_maps_conflict(client):
    respx.post(f"{BASE}/v1/webhooks").mock(
        return_value=httpx.Response(409, json={"error": "webhook_exists", "message": "taken"}))
    with pytest.raises(ResonaAPIError) as exc:
        client.create_webhook("backend", "https://hooks.example/resona")
    assert exc.value.code == "webhook_exists"


@respx.mock
def test_list_webhooks_returns_views_without_secret(client):
    item = {k: v for k, v in CREATED.items() if k != "secret"}
    respx.get(f"{BASE}/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": [item]}))
    assert client.list_webhooks() == [Webhook(**item)]


@respx.mock
def test_rotate_webhook_returns_new_secret(client):
    rotated = {**CREATED, "secret": "whsec_bmV3LXNlY3JldA=="}
    route = respx.post(f"{BASE}/v1/webhooks/backend/rotate").mock(
        return_value=httpx.Response(200, json=rotated))
    assert client.rotate_webhook("backend").secret == rotated["secret"]
    assert route.called


@respx.mock
def test_rotate_webhook_maps_not_found(client):
    respx.post(f"{BASE}/v1/webhooks/gone/rotate").mock(
        return_value=httpx.Response(404, json={"error": "webhook_not_found", "message": "no"}))
    with pytest.raises(ResonaAPIError) as exc:
        client.rotate_webhook("gone")
    assert (exc.value.status, exc.value.code) == (404, "webhook_not_found")


@respx.mock
def test_delete_webhook_sends_delete(client):
    route = respx.delete(f"{BASE}/v1/webhooks/backend").mock(return_value=httpx.Response(204))
    assert client.delete_webhook("backend") is None
    assert route.called


@respx.mock
def test_list_webhook_deliveries_sends_limit_and_parses_metadata(client):
    items = [
        {"id": "d2", "job_id": 8, "event": "job.failed", "attempts": 3, "last_status": 500,
         "delivered_at": None, "last_error": "http_status"},
        {"id": "d1", "job_id": 7, "event": "job.completed", "attempts": 1, "last_status": 204,
         "delivered_at": "2026-11-20T10:00:01Z", "last_error": None},
    ]
    route = respx.get(f"{BASE}/v1/webhooks/backend/deliveries").mock(
        return_value=httpx.Response(200, json={"items": items}))
    deliveries = client.list_webhook_deliveries("backend", limit=2)
    assert route.calls[0].request.url.params["limit"] == "2"
    assert deliveries == [WebhookDelivery(**items[0]), WebhookDelivery(**items[1])]


@respx.mock
def test_submit_job_sends_webhook_only_when_set(client, audio_file):
    respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
    jobs = respx.post(f"{BASE}/v1/jobs").mock(
        return_value=httpx.Response(202, json={"job_id": 1, "status": "pending"}))
    client.submit_job(audio_file, kind="audio")
    client.submit_job(audio_file, kind="audio", webhook="backend")
    client.submit_upload("upl_1", kind="audio", webhook="backend")
    sent = [json.loads(call.request.content) for call in jobs.calls]
    assert "webhook" not in sent[0]
    assert sent[1]["webhook"] == "backend"
    assert sent[2]["webhook"] == "backend"


@respx.mock
def test_submit_job_maps_unknown_webhook(client, audio_file):
    respx.post(f"{BASE}/v1/uploads").mock(return_value=httpx.Response(201, json=UPLOAD_JSON))
    respx.post(f"{BASE}/v1/jobs").mock(return_value=httpx.Response(
        422, json={"error": "unknown_webhook", "message": "no active webhook endpoint has this name"}))
    with pytest.raises(ResonaAPIError) as exc:
        client.submit_job(audio_file, kind="audio", webhook="missing")
    assert (exc.value.status, exc.value.code) == (422, "unknown_webhook")


@respx.mock
def test_create_transcription_sends_webhook_only_when_set(client, audio_file):
    route = respx.post(f"{BASE}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "hallo"}))
    client.create_transcription(audio_file)
    client.create_transcription(audio_file, webhook="backend")
    assert b'name="webhook"' not in route.calls[0].request.content
    assert b'name="webhook"\r\n\r\nbackend' in route.calls[1].request.content


# ── verify ────────────────────────────────────────────────────────────────────

NOW = 1_800_000_000


def _hub_secret() -> str:
    # The format the hub issues (resona_api.webhooks.new_secret).
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _event_body() -> bytes:
    event = JobEvent(
        type="job.completed", job_id=42, kind="audio", status="completed",
        finished_at=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
    )
    return event.model_dump_json().encode("utf-8")


def _signed(secret: str, body: bytes, *, timestamp: int = NOW, msg_id: str = "msg_1") -> dict[str, str]:
    # Signed with job-core's sign(), the function the hub's dispatcher uses.
    return {
        "Webhook-Id": msg_id,
        "Webhook-Timestamp": str(timestamp),
        "Webhook-Signature": sign(secret, msg_id, timestamp, body),
    }


def test_verify_accepts_hub_signed_body():
    secret, body = _hub_secret(), _event_body()
    event = verify(_signed(secret, body), body, secret, now=NOW)
    assert event.job_id == 42 and event.type == "job.completed"


def test_verify_accepts_any_listed_signature():
    secret, body = _hub_secret(), _event_body()
    headers = _signed(secret, body)
    headers["Webhook-Signature"] = f"v1,AAAA {headers['Webhook-Signature']}"
    assert verify(headers, body, secret, now=NOW).job_id == 42


def test_verify_uses_current_time_by_default():
    import time
    secret, body = _hub_secret(), _event_body()
    assert verify(_signed(secret, body, timestamp=int(time.time())), body, secret).job_id == 42


def test_verify_rejects_changed_body():
    secret, body = _hub_secret(), _event_body()
    headers = _signed(secret, body)
    with pytest.raises(InvalidWebhookSignature):
        verify(headers, body.replace(b"42", b"43"), secret, now=NOW)


def test_verify_rejects_wrong_secret():
    secret, body = _hub_secret(), _event_body()
    with pytest.raises(InvalidWebhookSignature):
        verify(_signed(secret, body), body, _hub_secret(), now=NOW)


@pytest.mark.parametrize("offset", [-301, 301])
def test_verify_rejects_stale_or_future_timestamp(offset):
    secret, body = _hub_secret(), _event_body()
    headers = _signed(secret, body, timestamp=NOW + offset)
    with pytest.raises(InvalidWebhookSignature):
        verify(headers, body, secret, now=NOW)
    # inside a wider tolerance the same request passes
    assert verify(headers, body, secret, now=NOW, tolerance_seconds=400).job_id == 42


def test_verify_rejects_changed_message_id():
    secret, body = _hub_secret(), _event_body()
    headers = _signed(secret, body)
    headers["Webhook-Id"] = "msg_2"
    with pytest.raises(InvalidWebhookSignature):
        verify(headers, body, secret, now=NOW)


@pytest.mark.parametrize("missing", ["Webhook-Id", "Webhook-Timestamp", "Webhook-Signature"])
def test_verify_rejects_missing_header(missing):
    secret, body = _hub_secret(), _event_body()
    headers = _signed(secret, body)
    del headers[missing]
    with pytest.raises(InvalidWebhookSignature):
        verify(headers, body, secret, now=NOW)


def test_verify_rejects_signed_body_that_is_not_a_job_event():
    secret, body = _hub_secret(), b'{"type": "job.completed", "transcript": "x"}'
    with pytest.raises(InvalidWebhookSignature):
        verify(_signed(secret, body), body, secret, now=NOW)
