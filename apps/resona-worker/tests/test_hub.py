"""HubClient error mapping, retries and fencing against an httpx mock transport."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from resona_job_core import Candidate, PolicyDenied

from resona_worker.hub import (
    HubClient,
    HubUnavailable,
    InvalidCredentials,
    ProtocolIncompatible,
    StaleLease,
    lease_deadline_mono,
)

HUB = "https://hub.test"
TOKEN = "wkr-hub-test-credential"
CANDIDATE = Candidate(engine="faster-whisper", model="tiny", accelerator="cpu")


def _client(handler, sleeps: list[float] | None = None) -> HubClient:
    record = sleeps if sleeps is not None else []
    return HubClient(
        HUB,
        TOKEN,
        transport=httpx.MockTransport(handler),
        sleep=record.append,
        rng=random.Random(1),
    )


def _claim_payload() -> dict:
    return {
        "job_id": 42,
        "kind": "audio",
        "lease_token": "lease-xyz",
        "lease_until": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
        "attempt": 2,
        "input_sha256": "ab" * 32,
        "execution_snapshot": {"kind": "audio", "candidate": CANDIDATE.model_dump(mode="json")},
    }


def test_claim_returns_none_on_no_work_and_job_on_200() -> None:
    responses = iter([httpx.Response(204), httpx.Response(200, json=_claim_payload())])
    client = _client(lambda request: next(responses))

    assert client.claim([CANDIDATE], wait=0) is None
    job = client.claim([CANDIDATE], wait=0)

    assert job is not None
    assert (job.job_id, job.lease_token, job.attempt) == (42, "lease-xyz", 2)


def test_claim_sends_bearer_token_and_capabilities_only_in_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    _client(handler).claim([CANDIDATE], wait=25)

    request = seen[0]
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.url.params["wait"] == "25"
    assert json.loads(request.content)["capabilities"] == [CANDIDATE.model_dump(mode="json")]


def test_rejected_credential_raises_without_leaking_token() -> None:
    client = _client(lambda request: httpx.Response(401, json={"error": "unauthorized", "message": "no"}))

    with pytest.raises(InvalidCredentials) as info:
        client.heartbeat([CANDIDATE], ready=True, runtime="0.0.0")

    assert TOKEN not in str(info.value)
    assert TOKEN not in repr(info.value)


def test_protocol_incompatibility_is_reported_as_426() -> None:
    client = _client(lambda request: httpx.Response(426, json={"error": "protocol_incompatible", "message": "v2"}))

    with pytest.raises(ProtocolIncompatible):
        client.heartbeat([CANDIDATE], ready=True, runtime="0.0.0")


def test_transient_server_errors_are_retried_then_succeed() -> None:
    attempts = iter([httpx.Response(503), httpx.Response(500), httpx.Response(204)])
    sleeps: list[float] = []
    client = _client(lambda request: next(attempts), sleeps)

    assert client.claim([CANDIDATE], wait=0) is None
    assert len(sleeps) == 2


def test_persistent_outage_becomes_hub_unavailable_after_bounded_attempts() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(HubUnavailable):
        _client(handler).claim([CANDIDATE], wait=0)
    assert calls == 4


def test_policy_refusal_raises_policy_denied_with_stage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "policy_denied", "message": "endpoint not approved"})

    client = _client(handler)
    with pytest.raises(PolicyDenied):
        client.policy_check(42, "lease-xyz", stage="polish", endpoint_id="cloud-llm")


def test_stale_lease_on_job_heartbeat_is_fenced() -> None:
    client = _client(lambda request: httpx.Response(409, json={"error": "stale_lease", "message": "expired"}))

    with pytest.raises(StaleLease):
        client.job_heartbeat(42, "lease-xyz", max_attempts=1)


def test_download_input_hashes_exactly_the_bytes_served(tmp_path: Path) -> None:
    payload = b"RIFF-synthetic-audio-bytes" * 100
    client = _client(lambda request: httpx.Response(200, content=payload))
    dest = tmp_path / "input.bin"

    digest = client.download_input(42, "lease-xyz", dest)

    assert dest.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()


def test_download_postprocess_sends_lease_and_returns_the_document() -> None:
    document = b'{"name":"fix","steps":[]}'
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=document)

    client = _client(handler)

    assert client.download_postprocess(42, "lease-xyz") == document
    assert seen[0].url.path == "/v1/workers/jobs/42/postprocess"
    assert seen[0].headers["X-Resona-Lease"] == "lease-xyz"


def test_lease_deadline_is_measured_from_request_start() -> None:
    lease_until = datetime.fromtimestamp(1120.0, tz=UTC)
    assert lease_deadline_mono(started_mono=100.0, started_wall=1000.0, lease_until=lease_until) == pytest.approx(220.0)
