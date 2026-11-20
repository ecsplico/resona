"""Webhook dispatcher: Standard Webhooks signing, retries, abandonment, ordering and the delivery log.

Rows are written straight into the outbox; ``clock.now`` is injected, so retry
schedules are checked without sleeping. Receivers are a real local HTTP server
(signature test) or an ``httpx.MockTransport`` handed to the dispatcher.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core.webhooks import JobEvent, secret_key, sign
from resona_api import clock, hublock, maintenance, webhooks
from resona_api.db.engine import engine
from resona_api.db.models import WebhookDelivery, WebhookEndpoint
from resona_api.releases import hub_release_id

T0 = datetime(2030, 1, 6, 9, 0, tzinfo=UTC)
LOCAL_URL = "http://127.0.0.1:9/hook"


@pytest.fixture
def at(monkeypatch):
    """``at(when)`` sets the injected clock."""
    def set_time(when: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: when)
    set_time(T0)
    return set_time


@pytest.fixture
def allow_private(monkeypatch):
    monkeypatch.setenv("RESONA_WEBHOOK_ALLOW_PRIVATE", "true")


def make_endpoint(org_id: str, url: str = LOCAL_URL, name: str = "app") -> WebhookEndpoint:
    with Session(engine) as session:
        endpoint = webhooks.new_endpoint(org_id, name, url)
        session.add(endpoint)
        session.commit()
        session.refresh(endpoint)
        session.expunge(endpoint)
        return endpoint


def add_delivery(org_id: str, endpoint_id: str, created: datetime = T0, *, job_id: int = 1, **fields) -> str:
    event = JobEvent(type="job.completed", job_id=job_id, kind="audio", status="completed", finished_at=created)
    delivery_id = uuid.uuid4().hex
    row = WebhookDelivery(
        id=delivery_id, organization_id=org_id, endpoint_id=endpoint_id, job_id=job_id,
        event=event.type, payload_json=event.model_dump_json(),
        created_at=created, next_attempt_at=fields.pop("next_attempt_at", created), **fields,
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()
    return delivery_id


def get_delivery(delivery_id: str) -> WebhookDelivery:
    with Session(engine) as session:
        row = session.get(WebhookDelivery, delivery_id)
        session.expunge(row)
        return row


def utc(value: datetime | None) -> datetime | None:
    return None if value is None else clock.as_utc(value)


class Recorder:
    """MockTransport handler answering from ``statuses`` (last one repeats) and recording requests."""

    def __init__(self, *statuses: int, headers: dict[str, str] | None = None) -> None:
        self.statuses = list(statuses) or [200]
        self.headers = headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return httpx.Response(status, headers=self.headers)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def dispatch(transport: httpx.AsyncBaseTransport | None = None) -> int:
    """One dispatcher pass, waiting for every sender it started."""
    async def go() -> int:
        dispatcher = webhooks.Dispatcher(transport)
        try:
            started = await dispatcher.tick()
            await dispatcher.wait_idle()
            return started
        finally:
            await dispatcher.aclose()
    return asyncio.run(go())


# ── signing and headers ─────────────────────────────────────────────────

class _Receiver(BaseHTTPRequestHandler):
    received: list[tuple[str, dict[str, str], bytes]] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def receiver():
    _Receiver.received = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Receiver.received
    server.shutdown()
    server.server_close()


def test_local_receiver_gets_post_signed_with_endpoint_secret(fresh_org, at, allow_private, receiver, caplog):
    base, received = receiver
    endpoint = make_endpoint(fresh_org, f"{base}/hook?token=query-secret")
    delivery_id = add_delivery(fresh_org, endpoint.id, job_id=42)
    caplog.set_level(logging.INFO)

    assert dispatch() == 1

    assert len(received) == 1
    path, headers, body = received[0]
    assert path == "/hook?token=query-secret"
    row = get_delivery(delivery_id)
    assert body == row.payload_json.encode()
    assert JobEvent.model_validate_json(body).job_id == 42
    assert headers["webhook-id"] == delivery_id
    timestamp = int(headers["webhook-timestamp"])
    assert timestamp == int(T0.timestamp())
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"] == f"resona-hub/{hub_release_id()}"
    # The receiver verifies with the endpoint secret, independently of the hub's signer.
    version, signature = headers["webhook-signature"].split(",", 1)
    expected = hmac.new(secret_key(endpoint.secret), f"{delivery_id}.{timestamp}.".encode() + body,
                        hashlib.sha256).digest()
    assert version == "v1" and base64.b64decode(signature) == expected
    assert headers["webhook-signature"] == sign(endpoint.secret, delivery_id, timestamp, body)

    assert utc(row.delivered_at) == T0 and row.attempts == 1 and row.last_status == 204 and row.last_error is None
    # Logs name the endpoint and delivery, never the query string or the secret.
    assert f"Webhook app delivery {delivery_id}: delivered" in caplog.text
    assert "query-secret" not in caplog.text
    assert endpoint.secret not in caplog.text


# ── retries ─────────────────────────────────────────────────────────────

def test_retry_delay_schedule_then_hourly():
    delays = [webhooks.retry_delay(n) for n in range(1, 9)]
    assert delays == [
        timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10),
        timedelta(minutes=30), timedelta(hours=1), timedelta(hours=1), timedelta(hours=1),
    ]


def test_500_twice_then_200_takes_three_attempts_on_schedule(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    delivery_id = add_delivery(fresh_org, endpoint.id)
    receiver = Recorder(500, 500, 200)

    dispatch(receiver.transport)
    row = get_delivery(delivery_id)
    assert len(receiver.requests) == 1
    assert row.attempts == 1 and row.last_status == 500 and row.last_error == "http_500"
    assert utc(row.next_attempt_at) == T0 + timedelta(seconds=5) and row.delivered_at is None

    at(T0 + timedelta(seconds=4))
    dispatch(receiver.transport)
    assert len(receiver.requests) == 1  # not due yet

    second = T0 + timedelta(seconds=5)
    at(second)
    dispatch(receiver.transport)
    row = get_delivery(delivery_id)
    assert len(receiver.requests) == 2 and row.attempts == 2
    assert utc(row.next_attempt_at) == second + timedelta(seconds=30)

    at(second + timedelta(seconds=29))
    dispatch(receiver.transport)
    assert len(receiver.requests) == 2

    third = second + timedelta(seconds=30)
    at(third)
    dispatch(receiver.transport)
    row = get_delivery(delivery_id)
    assert len(receiver.requests) == 3
    assert row.attempts == 3 and row.last_status == 200 and row.last_error is None
    assert utc(row.delivered_at) == third
    # Every attempt carried the same message id.
    assert {r.headers["webhook-id"] for r in receiver.requests} == {delivery_id}

    at(third + timedelta(hours=2))
    dispatch(receiver.transport)
    assert len(receiver.requests) == 3


def test_redirect_counts_as_failure_and_is_not_followed(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    delivery_id = add_delivery(fresh_org, endpoint.id)
    receiver = Recorder(302, headers={"location": "http://127.0.0.1:9/elsewhere"})

    dispatch(receiver.transport)

    assert [r.url.path for r in receiver.requests] == ["/hook"]
    row = get_delivery(delivery_id)
    assert row.delivered_at is None and row.attempts == 1
    assert row.last_status == 302 and row.last_error == "http_302"
    assert utc(row.next_attempt_at) == T0 + timedelta(seconds=5)


def test_timeout_and_connection_error_are_failures(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    delivery_id = add_delivery(fresh_org, endpoint.id)

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    dispatch(httpx.MockTransport(timeout))
    row = get_delivery(delivery_id)
    assert row.attempts == 1 and row.last_status is None and row.last_error == "timeout"

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    at(T0 + timedelta(seconds=5))
    dispatch(httpx.MockTransport(refused))
    row = get_delivery(delivery_id)
    assert row.attempts == 2 and row.last_error == "connection_error" and row.delivered_at is None


def test_url_failing_validation_at_send_time_is_not_contacted(fresh_org, at, allow_private, monkeypatch):
    endpoint = make_endpoint(fresh_org)  # registered while internal hosts were allowed
    delivery_id = add_delivery(fresh_org, endpoint.id)
    monkeypatch.delenv("RESONA_WEBHOOK_ALLOW_PRIVATE")
    receiver = Recorder(200)

    dispatch(receiver.transport)

    assert receiver.requests == []
    row = get_delivery(delivery_id)
    assert row.attempts == 1 and row.last_error == "invalid_url" and row.delivered_at is None


# ── abandonment ─────────────────────────────────────────────────────────

def test_rows_older_than_24_hours_are_abandoned(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    due = add_delivery(fresh_org, endpoint.id, T0)
    # Waiting for a retry far beyond the deadline: abandoned without coming due.
    waiting = add_delivery(fresh_org, endpoint.id, T0 + timedelta(seconds=1), attempts=7,
                           next_attempt_at=T0 + timedelta(hours=30))
    fresh = add_delivery(fresh_org, endpoint.id, T0 + timedelta(hours=1))
    receiver = Recorder(200)

    at(T0 + timedelta(hours=24, seconds=1))
    dispatch(receiver.transport)

    for delivery_id in (due, waiting):
        row = get_delivery(delivery_id)
        assert row.delivered_at is None and row.last_error == "expired"
    assert utc(get_delivery(fresh).delivered_at) is not None
    assert [r.headers["webhook-id"] for r in receiver.requests] == [fresh]

    at(T0 + timedelta(hours=48))
    dispatch(receiver.transport)
    assert len(receiver.requests) == 1  # abandoned rows are never sent


def test_revoked_endpoint_abandons_its_pending_rows(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    other = make_endpoint(fresh_org, name="other")
    due = add_delivery(fresh_org, endpoint.id)
    later = add_delivery(fresh_org, endpoint.id, next_attempt_at=T0 + timedelta(minutes=10))
    kept = add_delivery(fresh_org, other.id)
    with Session(engine) as session:
        row = session.get(WebhookEndpoint, endpoint.id)
        row.revoked_at = T0
        session.add(row)
        session.commit()
    receiver = Recorder(200)

    dispatch(receiver.transport)

    assert get_delivery(due).last_error == "endpoint_revoked"
    assert get_delivery(later).last_error == "endpoint_revoked"
    assert get_delivery(due).delivered_at is None and get_delivery(due).attempts == 0
    assert [r.headers["webhook-id"] for r in receiver.requests] == [kept]


# ── ordering and concurrency ────────────────────────────────────────────

def test_one_endpoint_serial_in_creation_order_others_concurrent(fresh_org, at, allow_private):
    slow = make_endpoint(fresh_org, "http://127.0.0.1:9/slow", name="slow")
    fast = make_endpoint(fresh_org, "http://127.0.0.1:9/fast", name="fast")
    # Created out of id order, two in the same instant: creation (insertion) order wins.
    slow_rows = [
        add_delivery(fresh_org, slow.id, T0 - timedelta(seconds=2)),
        add_delivery(fresh_org, slow.id, T0 - timedelta(seconds=1)),
        add_delivery(fresh_org, slow.id, T0 - timedelta(seconds=1)),
    ]
    fast_row = add_delivery(fresh_org, fast.id, T0)
    seen: list[str] = []
    state = {"in_flight": 0, "max_in_flight": 0}

    async def go():
        gate = asyncio.Event()
        fast_done = asyncio.Event()
        slow_started = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/fast":
                fast_done.set()
                return httpx.Response(200)
            state["in_flight"] += 1
            slow_started.set()
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            seen.append(request.headers["webhook-id"])
            await gate.wait()
            state["in_flight"] -= 1
            return httpx.Response(200)

        dispatcher = webhooks.Dispatcher(httpx.MockTransport(handler))
        try:
            assert await dispatcher.tick() == 2
            await asyncio.wait_for(fast_done.wait(), 5)
            await asyncio.wait_for(slow_started.wait(), 5)
            # The slow endpoint holds its first request; the fast one was not held up.
            assert seen == slow_rows[:1]
            assert await dispatcher.tick() == 0  # a busy endpoint gets no second sender
            gate.set()
            await dispatcher.wait_idle()
        finally:
            await dispatcher.aclose()

    asyncio.run(go())
    assert seen == slow_rows
    assert state["max_in_flight"] == 1
    assert all(get_delivery(i).delivered_at is not None for i in [*slow_rows, fast_row])


def test_failed_head_blocks_later_rows_of_its_endpoint(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    head = add_delivery(fresh_org, endpoint.id, T0 - timedelta(seconds=1))
    later = add_delivery(fresh_org, endpoint.id, T0)
    receiver = Recorder(500, 200)

    dispatch(receiver.transport)
    assert [r.headers["webhook-id"] for r in receiver.requests] == [head]
    assert get_delivery(later).attempts == 0

    at(T0 + timedelta(seconds=5))
    dispatch(receiver.transport)
    assert [r.headers["webhook-id"] for r in receiver.requests] == [head, head, later]
    assert get_delivery(later).delivered_at is not None


# ── durability ──────────────────────────────────────────────────────────

def test_restart_resumes_pending_rows(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    delivery_id = add_delivery(fresh_org, endpoint.id)

    async def go():
        started = asyncio.Event()

        async def hang(request):
            started.set()
            await asyncio.Event().wait()

        first = webhooks.Dispatcher(httpx.MockTransport(hang))
        await first.tick()
        await asyncio.wait_for(started.wait(), 5)
        await first.aclose()  # hub stops mid-send

        row = get_delivery(delivery_id)
        assert row.delivered_at is None and row.attempts == 0 and row.last_error is None

        receiver = Recorder(200)
        second = webhooks.Dispatcher(receiver.transport)  # the restarted hub
        try:
            assert await second.tick() == 1
            await second.wait_idle()
        finally:
            await second.aclose()
        return receiver

    receiver = asyncio.run(go())
    assert [r.headers["webhook-id"] for r in receiver.requests] == [delivery_id]
    assert get_delivery(delivery_id).delivered_at is not None


# ── delivery log ────────────────────────────────────────────────────────

def test_deliveries_route_lists_metadata_without_payload_or_secret(
    test_app, fresh_org, at, allow_private, admin_headers, client_headers,
):
    admin = admin_headers(fresh_org)
    with TestClient(test_app) as http:
        created = http.post("/v1/webhooks", json={"name": "app", "url": LOCAL_URL}, headers=admin)
        assert created.status_code == 201, created.text
        secret = created.json()["secret"]
        with Session(engine) as session:
            endpoint_id = webhooks.get_endpoint(session, fresh_org, "app").id
        older = add_delivery(fresh_org, endpoint_id, T0 - timedelta(minutes=1), job_id=7,
                             delivered_at=T0, attempts=1, last_status=204)
        newer = add_delivery(fresh_org, endpoint_id, T0, job_id=8, attempts=2, last_status=500,
                             last_error="http_500")

        resp = http.get("/v1/webhooks/app/deliveries", headers=admin)
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert [item["id"] for item in items] == [newer, older]
        assert items[0] == {
            "id": newer, "job_id": 8, "event": "job.completed", "attempts": 2,
            "last_status": 500, "delivered_at": None, "last_error": "http_500",
        }
        assert items[1]["delivered_at"].startswith("2030-01-06T09:00:00")
        assert secret not in resp.text
        assert "finished_at" not in resp.text and "payload" not in resp.text and LOCAL_URL not in resp.text

        limited = http.get("/v1/webhooks/app/deliveries?limit=1", headers=admin)
        assert [item["id"] for item in limited.json()["items"]] == [newer]
        assert http.get("/v1/webhooks/app/deliveries?limit=0", headers=admin).status_code == 422
        assert http.get("/v1/webhooks/app/deliveries?limit=101", headers=admin).status_code == 422
        assert http.get("/v1/webhooks/nope/deliveries", headers=admin).status_code == 404
        assert http.get("/v1/webhooks/app/deliveries", headers=client_headers(fresh_org)).status_code == 403


def test_maintenance_deletes_delivery_rows_after_seven_days(fresh_org, at, allow_private):
    endpoint = make_endpoint(fresh_org)
    old = add_delivery(fresh_org, endpoint.id, T0, delivered_at=T0, attempts=1, last_status=200)
    recent = add_delivery(fresh_org, endpoint.id, T0 + timedelta(days=1), delivered_at=T0, attempts=1)

    at(T0 + timedelta(days=7) - timedelta(seconds=1))
    assert maintenance.run_once()["webhook_deliveries_deleted"] == 0
    at(T0 + timedelta(days=7))
    assert maintenance.run_once()["webhook_deliveries_deleted"] == 1
    with Session(engine) as session:
        assert session.get(WebhookDelivery, old) is None
        assert session.get(WebhookDelivery, recent) is not None


# ── lifespan ────────────────────────────────────────────────────────────

def test_lifespan_starts_and_cleanly_cancels_the_dispatcher(monkeypatch):
    from resona_api.app import app

    state: dict[str, bool] = {}
    original = webhooks.dispatch_loop

    async def tracked(*args, **kwargs):
        state["started"] = True
        try:
            await original(*args, **kwargs)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(webhooks, "dispatch_loop", tracked)
    monkeypatch.setattr(maintenance, "last_tick", None)
    with TestClient(app):
        deadline = time.monotonic() + 10
        while not state.get("started") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert state.get("started")
    assert state.get("cancelled")
    hublock.acquire().release()  # shutdown completed and released the hub lock
