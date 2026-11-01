"""Liveness, readiness, the single-hub lock and log redaction."""

from __future__ import annotations

import logging
import logging.config
import time
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_api import clock, health_routes, hublock, maintenance
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import Worker
from resona_api.hublock import HubRunning
from resona_api.run import RedactQueryStrings, log_config

FUTURE = clock.now() + timedelta(days=3650)


@pytest.fixture
def probes():
    app = FastAPI()
    app.include_router(health_routes.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def later(monkeypatch):
    """A time when no worker from other tests is still online; maintenance ticked just now."""
    monkeypatch.setattr(clock, "now", lambda: FUTURE)
    monkeypatch.setattr(maintenance, "last_tick", FUTURE)


def test_health_is_plain_liveness(probes):
    resp = probes.get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_ready_with_offline_workers_is_ready_but_degraded(probes, later):
    resp = probes.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"schema": True, "data_writable": True, "maintenance": True}
    assert body["capacity"] == "degraded"


def test_capacity_available_while_an_approved_worker_is_online(probes, later, fresh_org):
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "probe-worker", fresh_org)
        approve_worker(session, worker_id, private=True, capabilities=[])
        worker = session.get(Worker, worker_id)
        worker.last_seen = FUTURE
        session.add(worker)
        session.commit()
    assert probes.get("/ready").json()["capacity"] == "available"


@pytest.mark.parametrize("tick", [None, FUTURE - timedelta(minutes=5)])
def test_ready_fails_without_a_recent_maintenance_pass(probes, later, monkeypatch, tick):
    monkeypatch.setattr(maintenance, "last_tick", tick)
    resp = probes.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["maintenance"] is False


def test_ready_fails_on_unwritable_data(probes, later, monkeypatch, tmp_path):
    monkeypatch.setattr(health_routes, "DATA_PATH", tmp_path / "missing")
    resp = probes.get("/ready")
    assert resp.status_code == 503 and resp.json()["checks"]["data_writable"] is False


def test_ready_fails_on_outdated_schema(probes, later, monkeypatch):
    monkeypatch.setattr(health_routes, "TARGET_VERSION", health_routes.TARGET_VERSION + 1)
    resp = probes.get("/ready")
    assert resp.status_code == 503 and resp.json()["checks"]["schema"] is False


def test_running_hub_is_ready_and_holds_the_hub_lock(monkeypatch):
    from resona_api.app import app

    monkeypatch.setattr(maintenance, "last_tick", None)
    with TestClient(app) as c:
        deadline = time.monotonic() + 10
        while maintenance.last_tick is None and time.monotonic() < deadline:
            time.sleep(0.05)
        resp = c.get("/ready")
        assert resp.status_code == 200, resp.text
        with pytest.raises(HubRunning):
            hublock.acquire()
    hublock.acquire().release()  # released on shutdown


def test_log_filter_drops_query_strings():
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
        ("10.0.0.1:5000", "/v1/listen?token=s3cret&model=x"), None,
    )
    assert RedactQueryStrings().filter(record)
    assert "s3cret" not in record.getMessage()
    assert "/v1/listen?<redacted>" in record.getMessage()


def test_uvicorn_log_config_redacts_on_every_handler():
    cfg = log_config()
    assert all("redact_query" in handler["filters"] for handler in cfg["handlers"].values())
    logging.config.dictConfig(cfg)
