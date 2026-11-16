"""Unauthenticated probes: ``/health`` (liveness) and ``/ready`` (readiness).

``/ready`` answers 503 unless the schema is at the target version, the data and
database directories are writable and the maintenance loop finished a pass
recently. Offline workers never fail readiness: they only lower ``capacity`` to
``degraded``, because jobs wait in the queue until an approved worker returns.
Neither probe reveals organizations, workers or job counts.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from . import clock, maintenance, schemas
from .db.engine import engine
from .db.models import Worker
from .db.schema import TARGET_VERSION
from .jobs_routes import WORKER_ONLINE_WINDOW
from .openapi import CLIENT
from .paths import DATA_PATH, DB_PATH

router = APIRouter(tags=[CLIENT, "Health"])

MAINTENANCE_MAX_AGE = timedelta(seconds=3 * maintenance.INTERVAL_SECONDS)


@router.get("/health", operation_id="health_get", response_model=schemas.HealthView)
def health() -> dict[str, str]:
    """Liveness: the process serves HTTP. Checks nothing else."""
    return {"status": "ok"}


def _schema_current() -> bool:
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
    except Exception:
        return False
    return version == TARGET_VERSION


def _writable(directory: Path) -> bool:
    try:
        fd, name = tempfile.mkstemp(prefix=".ready-", dir=directory)
    except OSError:
        return False
    os.close(fd)
    os.unlink(name)
    return True


def _maintenance_alive() -> bool:
    tick = maintenance.last_tick
    return tick is not None and clock.now() - tick <= MAINTENANCE_MAX_AGE


def _capacity() -> str:
    """``available`` when any approved, unrevoked worker was seen recently, else ``degraded``."""
    cutoff = clock.now() - WORKER_ONLINE_WINDOW
    try:
        with Session(engine) as session:
            workers = session.exec(
                select(Worker).where(Worker.approved.is_(True), Worker.revoked_at.is_(None))
            ).all()
    except Exception:
        return "degraded"
    online = any(w.last_seen is not None and clock.as_utc(w.last_seen) >= cutoff for w in workers)
    return "available" if online else "degraded"


@router.get(
    "/ready", operation_id="ready_get", response_model=schemas.ReadyView,
    responses={503: {"model": schemas.ReadyView, "description": "Not ready: a check failed (`status: not_ready`)."}},
)
def ready() -> JSONResponse:
    checks = {
        "schema": _schema_current(),
        "data_writable": _writable(DATA_PATH) and _writable(DB_PATH),
        "maintenance": _maintenance_alive(),
    }
    ok = all(checks.values())
    body: dict[str, Any] = {
        "status": "ready" if ok else "not_ready",
        "checks": checks,
        "capacity": _capacity(),
    }
    return JSONResponse(body, status_code=200 if ok else 503)


__all__ = ["MAINTENANCE_MAX_AGE", "router"]
