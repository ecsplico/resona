"""Outbound pull-worker routes: heartbeat, long-poll claim, and lease-fenced job calls.

Every call authenticates the worker credential (``worker_principal``). Lease-bound
calls additionally present ``X-Resona-Lease``; the broker checks organization,
approval, revocation, lease currency and cancellation under a write lock.
Broker rejections surface as ``{"error": <code>, "message": <text>}`` bodies.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi import Path as FastAPIPath
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from resona_job_core import Candidate
from resona_job_core.release import RELEASE_ID_PATTERN

from . import broker, releases, storage
from .auth import Principal, worker_principal
from .broker import BrokerError
from .db.models import Release

router = APIRouter(prefix="/v1/workers", tags=["workers"])

MAX_RESULT_BYTES = 200 * 1024 * 1024
MAX_LONG_POLL_SECONDS = 25
LONG_POLL_TICK_SECONDS = 0.5
CODE_PATTERN = r"^[a-z_]{1,64}$"
PROTOCOL_HEADER = "X-Resona-Worker-Protocol"
_HEX = frozenset("0123456789abcdef")


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[Candidate] = Field(default_factory=list)
    # Also offer text (postprocess-only) jobs; older workers omit it and never receive them.
    text_jobs: bool = False
    # Also offer diarize jobs; true only when the worker's prepare verified the diarizer.
    diarize: bool = False


class FailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=CODE_PATTERN)
    message: str = Field(default="", max_length=4000)
    retryable: bool = False
    withdraw_capability: Optional[Candidate] = None


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=64)
    endpoint_id: Optional[str] = Field(default=None, max_length=128)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    runtime_version: str = Field(default="", max_length=128)
    reported_capabilities: list[Candidate] = Field(default_factory=list)
    text_jobs: bool = False
    diarize: bool = False
    # Job features the worker's release knows (resona_job_core.features); omitted by
    # older workers, whose release then decides.
    features: Optional[list[Annotated[str, Field(min_length=1, max_length=128)]]] = Field(
        default=None, max_length=1024,
    )


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _worker_id(principal: Principal) -> str:
    if principal.worker_id is None:
        raise _error(401, "unauthorized", "credential is not bound to a worker")
    return principal.worker_id


def _lease(principal: Principal, job_id: int, lease_token: Optional[str]) -> broker.LeaseRef:
    return broker.LeaseRef(
        worker_id=_worker_id(principal),
        organization_id=principal.organization_id,
        job_id=job_id,
        lease_token=lease_token or "",
    )


def _run_with_session(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Run one broker call with its own session; executed in a worker thread."""
    from .db.engine import engine

    with Session(engine) as session:
        return fn(session, **kwargs)


async def _broker(fn: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(_run_with_session, fn, **kwargs)
    except BrokerError as exc:
        raise _error(exc.status, exc.code, exc.message) from exc


def _protocol_major(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        head = value.strip().split(".", 1)[0]
        return int(head) if head.isdigit() else None
    return None


@router.post("/heartbeat")
async def heartbeat(request: Request, principal: Principal = Depends(worker_principal)) -> dict[str, Any]:
    """Record readiness and runtime version. The protocol major is checked before anything else."""
    try:
        raw = await request.json()
    except ValueError as exc:
        raise _error(422, "invalid_body", "heartbeat body must be JSON") from exc
    if not isinstance(raw, dict):
        raise _error(422, "invalid_body", "heartbeat body must be an object")
    if _protocol_major(raw.get("worker_protocol")) != broker.PROTOCOL_VERSION:
        raise _error(426, "protocol_incompatible", "worker protocol version is not supported")
    try:
        body = HeartbeatRequest.model_validate(raw)
    except ValueError as exc:
        raise _error(422, "invalid_body", "heartbeat body is invalid") from exc
    return await _broker(
        broker.record_heartbeat,
        worker_id=_worker_id(principal),
        organization_id=principal.organization_id,
        runtime_version=body.runtime_version,
        reported=body.reported_capabilities,
        text_jobs=body.text_jobs,
        diarize=body.diarize,
        features=body.features,
    )


def compatible_worker(
    principal: Principal = Depends(worker_principal),
    x_resona_worker_protocol: Optional[str] = Header(default=None, alias=PROTOCOL_HEADER),
) -> Principal:
    """Authenticated worker whose ``X-Resona-Worker-Protocol`` major matches the hub's.

    Every route except the heartbeat (which carries the version in its body) requires
    the header, so an incompatible worker gets 426 before it can claim or download.
    """
    if _protocol_major(x_resona_worker_protocol) != broker.PROTOCOL_VERSION:
        raise _error(426, "protocol_incompatible", "worker protocol version is not supported")
    return principal


@router.get("/releases/{release_id}")
async def release_status(
    release_id: str = FastAPIPath(pattern=RELEASE_ID_PATTERN),
    principal: Principal = Depends(compatible_worker),
) -> dict[str, Any]:
    """What this worker's organization may do with one release (``resona-worker update``)."""

    def lookup(session: Session) -> dict[str, Any] | None:
        row = session.get(Release, release_id)
        if row is None:
            return None
        return releases.release_view(session, row, principal.organization_id)

    view = await run_in_threadpool(_run_with_session, lookup)
    if view is None:
        raise _error(404, "release_not_found", "release is not staged on this hub")
    return view


@router.post("/claim", response_model=None)
async def claim(
    body: ClaimRequest,
    wait: int = Query(default=0, ge=0, le=MAX_LONG_POLL_SECONDS),
    principal: Principal = Depends(compatible_worker),
) -> Response:
    """Claim the oldest eligible job, long-polling up to ``wait`` seconds. 204 when none."""
    worker_id = _worker_id(principal)
    deadline = time.monotonic() + wait
    while True:
        result = await _broker(
            broker.try_claim,
            worker_id=worker_id,
            organization_id=principal.organization_id,
            capabilities=body.capabilities,
            text_jobs=body.text_jobs,
            diarize=body.diarize,
        )
        if result is not None:
            return JSONResponse(result.to_json(), status_code=200)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Response(status_code=204)
        await asyncio.sleep(min(LONG_POLL_TICK_SECONDS, remaining))


@router.get("/jobs/{job_id}/input")
async def job_input(
    job_id: int,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    principal: Principal = Depends(compatible_worker),
) -> FileResponse:
    path: Path = await _broker(broker.input_path, lease=_lease(principal, job_id, x_resona_lease))
    return FileResponse(path, media_type="application/octet-stream")


@router.get("/jobs/{job_id}/postprocess")
async def job_postprocess(
    job_id: int,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    principal: Principal = Depends(compatible_worker),
) -> FileResponse:
    """The resolved postprocess profile named by the job's execution snapshot."""
    path: Path = await _broker(broker.postprocess_path, lease=_lease(principal, job_id, x_resona_lease))
    return FileResponse(path, media_type="application/json")


@router.post("/jobs/{job_id}/heartbeat")
async def job_heartbeat(
    job_id: int,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    principal: Principal = Depends(compatible_worker),
) -> dict[str, Any]:
    return await _broker(broker.heartbeat_job, lease=_lease(principal, job_id, x_resona_lease))


@router.post("/jobs/{job_id}/policy")
async def job_policy(
    job_id: int,
    body: PolicyRequest,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    principal: Principal = Depends(compatible_worker),
) -> dict[str, Any]:
    """Fresh policy check against current organization settings before an external stage."""
    await _broker(
        broker.check_stage,
        lease=_lease(principal, job_id, x_resona_lease),
        stage=body.stage,
        endpoint_id=body.endpoint_id,
    )
    return {"allowed": True}


@router.post("/jobs/{job_id}/result")
async def job_result(
    job_id: int,
    request: Request,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    x_resona_result_sha256: Optional[str] = Header(default=None, alias="X-Resona-Result-Sha256"),
    principal: Principal = Depends(compatible_worker),
) -> dict[str, Any]:
    """Accept a JSON result: stream to an attempt temp file, verify, validate, then complete."""
    lease = _lease(principal, job_id, x_resona_lease)
    expected = (x_resona_result_sha256 or "").lower()
    if len(expected) != 64 or any(ch not in _HEX for ch in expected):
        raise _error(422, "checksum_required", "X-Resona-Result-Sha256 must be a SHA-256 hex digest")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_RESULT_BYTES:
        raise _error(413, "too_large", "result exceeds the 200 MiB limit")

    slot = await _broker(broker.begin_result, lease=lease, sha256=expected)
    if slot is None:
        # Accepted duplicate of the same lease and content: idempotent success.
        return {"status": "completed"}

    directory = storage.attempt_dir(slot.storage_id, job_id, slot.attempt)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f"result-{secrets.token_hex(8)}.tmp"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_RESULT_BYTES:
                    raise _error(413, "too_large", "result exceeds the 200 MiB limit")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if digest.hexdigest() != expected:
            raise _error(422, "checksum_mismatch", "result body does not match its checksum")
        try:
            await run_in_threadpool(broker.parse_result, temp, slot.kind)
        except BrokerError as exc:
            raise _error(exc.status, exc.code, exc.message) from exc
        status = await _broker(
            broker.finish_result,
            lease=lease,
            sha256=expected,
            temp_path=temp,
            size=size,
        )
    except BaseException:
        storage.remove_file(temp)
        raise
    return {"status": status}


@router.post("/jobs/{job_id}/fail")
async def job_fail(
    job_id: int,
    body: FailRequest,
    x_resona_lease: Optional[str] = Header(default=None, alias="X-Resona-Lease"),
    principal: Principal = Depends(compatible_worker),
) -> dict[str, Any]:
    status = await _broker(
        broker.fail_job,
        lease=_lease(principal, job_id, x_resona_lease),
        code=body.code,
        message=body.message,
        retryable=body.retryable,
        withdraw=body.withdraw_capability,
    )
    return {"status": status}
