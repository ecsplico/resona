"""HTTP client for the hub's worker routes (contract v1).

Every call authenticates with the worker bearer token. Errors are mapped to
typed exceptions carrying only the hub's machine-readable code; the token and
response bodies are never embedded in exception text.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field
from resona_job_core import Candidate, PolicyDenied

from . import WORKER_PROTOCOL

log = logging.getLogger("resona_worker.hub")
MAX_RESULT_BYTES = 200 * 1024 * 1024
MAX_INPUT_BYTES = 200 * 1024 * 1024
MAX_POSTPROCESS_BYTES = 1024 * 1024
_CHUNK = 1024 * 1024
_REQUEST_TIMEOUT = httpx.Timeout(60.0)
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 30.0

T = TypeVar("T")


class HubError(Exception):
    """Base for hub failures. ``code`` is the hub's machine-readable error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InvalidCredentials(HubError):
    """Hub rejected the worker credential (401/403)."""


class ProtocolIncompatible(HubError):
    """Hub speaks an incompatible worker protocol major (426)."""


class StaleLease(HubError):
    """The lease is expired, replaced or fenced (409 stale_lease)."""


class HubRejected(HubError):
    """Any other non-retryable hub answer."""


class HubUnavailable(HubError):
    """Transport failures or 5xx persisted past the retry budget."""

    def __init__(self) -> None:
        super().__init__("hub_unavailable")


class InputTooLarge(Exception):
    """Downloaded input exceeded the local limit."""


class _Transient(Exception):
    """Internal: retryable failure inside one operation."""


class HeartbeatReply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    worker_id: str | int
    approved: bool
    approved_private: bool = False
    approved_capabilities: list[Candidate] = Field(default_factory=list)


class ClaimedJob(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: int
    kind: str
    lease_token: str = Field(repr=False)
    lease_until: datetime
    attempt: int
    input_sha256: str
    execution_snapshot: dict[str, Any]


class JobHeartbeat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lease_until: datetime
    cancel_requested: bool = False


class ReleaseStatus(BaseModel):
    """``GET /v1/workers/releases/{id}``: whether this organization may run a release."""

    model_config = ConfigDict(extra="ignore")

    release_id: str
    status: str
    manifest_sha256: str
    bundled: bool = False
    approved_tuples: list[dict[str, str]] = Field(default_factory=list)


def lease_deadline_mono(started_mono: float, started_wall: float, lease_until: datetime) -> float:
    """Monotonic deadline for a lease, measured from a request started before the server replied.

    Starting from the pre-request timestamps is conservative: the server's lease
    began at or after that moment, so the local deadline never outlives it.
    """
    remaining = lease_until.timestamp() - started_wall
    return started_mono + remaining


def _backoff(attempt: int, rng: random.Random) -> float:
    base = min(_BACKOFF_MAX, float(2**attempt))
    return min(_BACKOFF_MAX, max(_BACKOFF_MIN, base * rng.uniform(0.8, 1.2)))


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}"
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) and code else f"http_{response.status_code}"


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    if status >= 500:
        raise _Transient()
    code = _error_code(response)
    if status in (401, 403):
        raise InvalidCredentials(code)
    if status == 426:
        raise ProtocolIncompatible(code)
    if status == 409 and code == "stale_lease":
        raise StaleLease(code)
    raise HubRejected(code)


class HubClient:
    """Worker-side client. Thread-safe for the lease keeper's heartbeat thread."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = lambda seconds: None,
        rng: random.Random | None = None,
    ) -> None:
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": str(WORKER_PROTOCOL)},
            timeout=_REQUEST_TIMEOUT,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _retrying(self, op: Callable[[], T], *, max_attempts: int) -> T:
        for attempt in range(max_attempts):
            try:
                return op()
            except (_Transient, httpx.TransportError) as exc:
                if attempt + 1 >= max_attempts:
                    log.warning("hub unavailable after %d attempts (%s)", max_attempts, exc.__class__.__name__)
                    raise HubUnavailable() from None
                self._sleep(_backoff(attempt, self._rng))
        raise HubUnavailable()  # pragma: no cover - loop always returns or raises

    # -- registration -------------------------------------------------------

    def heartbeat(
        self, capabilities: list[Candidate], *, ready: bool, runtime: str, diarize: bool = False
    ) -> HeartbeatReply:
        body = {
            "runtime_version": runtime,
            "worker_protocol": WORKER_PROTOCOL,
            "reported_capabilities": [c.model_dump(mode="json") for c in capabilities],
            "ready": ready,
            # Every worker runs text (postprocess-only) jobs: they need no ML dependencies.
            "text_jobs": True,
        }
        if diarize:
            # Sent only when true, so hubs predating diarization keep accepting the body.
            body["diarize"] = True

        def op() -> HeartbeatReply:
            response = self._client.post("/v1/workers/heartbeat", json=body)
            _raise_for_status(response)
            return HeartbeatReply.model_validate(response.json())

        return self._retrying(op, max_attempts=4)

    def release(self, release_id: str) -> ReleaseStatus | None:
        """The hub's view of one release for this worker's organization; None when not staged."""

        def op() -> ReleaseStatus | None:
            response = self._client.get(f"/v1/workers/releases/{release_id}")
            if response.status_code == 404:
                return None
            _raise_for_status(response)
            return ReleaseStatus.model_validate(response.json())

        return self._retrying(op, max_attempts=4)

    # -- claiming -----------------------------------------------------------

    def claim(self, capabilities: list[Candidate], *, wait: int, diarize: bool = False) -> ClaimedJob | None:
        body: dict[str, object] = {"capabilities": [c.model_dump(mode="json") for c in capabilities], "text_jobs": True}
        if diarize:
            body["diarize"] = True

        def op() -> ClaimedJob | None:
            response = self._client.post(
                "/v1/workers/claim",
                params={"wait": wait},
                json=body,
            )
            _raise_for_status(response)
            if response.status_code == 204:
                return None
            return ClaimedJob.model_validate(response.json())

        return self._retrying(op, max_attempts=4)

    # -- lease-scoped calls -------------------------------------------------

    def download_input(self, job_id: int, lease_token: str, dest: Path) -> str:
        """Stream the input to ``dest`` and return its SHA-256 hex digest."""

        def op() -> str:
            digest = hashlib.sha256()
            total = 0
            with dest.open("wb") as fh, self._client.stream(
                "GET",
                f"/v1/workers/jobs/{job_id}/input",
                headers={"X-Resona-Lease": lease_token},
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    _raise_for_status(response)
                for chunk in response.iter_bytes(_CHUNK):
                    total += len(chunk)
                    if total > MAX_INPUT_BYTES:
                        raise InputTooLarge()
                    digest.update(chunk)
                    fh.write(chunk)
            return digest.hexdigest()

        return self._retrying(op, max_attempts=4)

    def download_postprocess(self, job_id: int, lease_token: str) -> bytes:
        """Fetch the job's resolved postprocess profile document (raw bytes, unparsed)."""

        def op() -> bytes:
            body = bytearray()
            with self._client.stream(
                "GET",
                f"/v1/workers/jobs/{job_id}/postprocess",
                headers={"X-Resona-Lease": lease_token},
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    _raise_for_status(response)
                for chunk in response.iter_bytes(_CHUNK):
                    body.extend(chunk)
                    if len(body) > MAX_POSTPROCESS_BYTES:
                        raise InputTooLarge()
            return bytes(body)

        return self._retrying(op, max_attempts=4)

    def job_heartbeat(self, job_id: int, lease_token: str, *, max_attempts: int = 4) -> JobHeartbeat:
        def op() -> JobHeartbeat:
            response = self._client.post(
                f"/v1/workers/jobs/{job_id}/heartbeat",
                headers={"X-Resona-Lease": lease_token},
            )
            _raise_for_status(response)
            return JobHeartbeat.model_validate(response.json())

        return self._retrying(op, max_attempts=max_attempts)

    def policy_check(self, job_id: int, lease_token: str, *, stage: str, endpoint_id: str | None) -> None:
        """Fresh hub check before an external stage. Raises PolicyDenied when refused."""
        body = {"stage": stage, "endpoint_id": endpoint_id}

        def op() -> None:
            response = self._client.post(
                f"/v1/workers/jobs/{job_id}/policy",
                headers={"X-Resona-Lease": lease_token},
                json=body,
            )
            if response.status_code == 403 and _error_code(response) == "policy_denied":
                raise PolicyDenied(stage, "denied by hub policy")
            _raise_for_status(response)

        self._retrying(op, max_attempts=3)

    def upload_result(
        self,
        job_id: int,
        lease_token: str,
        *,
        sha256: str,
        size: int,
        body_path: Path,
    ) -> None:
        """Upload the validated result file. Each attempt streams a fresh read of the file."""

        def chunks() -> Iterator[bytes]:
            with body_path.open("rb") as fh:
                while block := fh.read(_CHUNK):
                    yield block

        def op() -> None:
            response = self._client.post(
                f"/v1/workers/jobs/{job_id}/result",
                headers={
                    "X-Resona-Lease": lease_token,
                    "X-Resona-Result-Sha256": sha256,
                    "Content-Type": "application/json",
                    "Content-Length": str(size),
                },
                content=chunks(),
            )
            _raise_for_status(response)

        self._retrying(op, max_attempts=4)

    def fail(
        self,
        job_id: int,
        lease_token: str,
        *,
        code: str,
        message: str,
        retryable: bool,
        withdraw_capability: Candidate | None,
    ) -> None:
        body = {
            "code": code,
            "message": message[:500],
            "retryable": retryable,
            "withdraw_capability": (
                withdraw_capability.model_dump(mode="json") if withdraw_capability else None
            ),
        }

        def op() -> None:
            response = self._client.post(
                f"/v1/workers/jobs/{job_id}/fail",
                headers={"X-Resona-Lease": lease_token},
                json=body,
            )
            _raise_for_status(response)

        self._retrying(op, max_attempts=4)


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while block := fh.read(_CHUNK):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


__all__ = [
    "ClaimedJob",
    "HeartbeatReply",
    "HubClient",
    "HubError",
    "HubRejected",
    "HubUnavailable",
    "InputTooLarge",
    "InvalidCredentials",
    "JobHeartbeat",
    "MAX_INPUT_BYTES",
    "MAX_RESULT_BYTES",
    "ProtocolIncompatible",
    "ReleaseStatus",
    "StaleLease",
    "lease_deadline_mono",
    "sha256_file",
]
