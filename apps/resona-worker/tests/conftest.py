"""Shared fixtures: a fake hub on ``httpx.MockTransport`` and a stub executor.

The fake hub implements only the worker routes of contract v1. The stub
executor is a real subprocess whose behaviour is selected by the input bytes,
so the supervisor's subprocess handling is exercised end to end.
"""

from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from resona_job_core import Candidate

from resona_worker.config import WorkerConfig
from resona_worker.hub import HubClient
from resona_worker.supervisor import Supervisor

HUB = "https://hub.test"
TOKEN = "wkr-test-credential-7f3a9c"

EXECUTOR_SCRIPT = r'''
import argparse, json, os, signal, sys, time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
data = Path(args.input).read_bytes()
if data == b"BADINPUT":
    sys.exit(2)
if data == b"ENGINE":
    sys.exit(3)
if data == b"NOCAP":
    sys.exit(4)
if data == b"OOM":
    os.kill(os.getpid(), signal.SIGKILL)
if data == b"HANG":
    time.sleep(600)
snapshot = json.loads(Path(args.snapshot).read_text())
cand = snapshot["candidate"]
result = {
    "schema_version": 1,
    "kind": snapshot["kind"],
    "raw_text": "hallo welt",
    "markdown": "hallo welt",
    "language": "de",
    "segments": [],
    "pages": [],
    "warnings": [],
    "provenance": {"stages": [{
        "stage": "asr", "engine": cand["engine"], "model": cand["model"],
        "model_revision": cand["model_revision"], "accelerator": cand["accelerator"],
        "seconds": 0.01,
    }]},
}
Path(args.out).write_text(json.dumps(result))
'''


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def snapshot_for(
    candidate: Candidate,
    *,
    kind: str = "audio",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "candidate": candidate.model_dump(mode="json"),
        "language": "de",
        "options": {},
        "settings_revision": 3,
        "policy": policy or {"private": True, "cloud_enabled": False, "approved_endpoint_ids": [], "endpoints": []},
        "postprocess": None,
    }


class FakeHub:
    """Stateful stand-in for the hub's worker routes. Records every call.

    ``profile`` is served from the postprocess route; the snapshot then names it
    by ``{name, revision, sha256}`` only, as the hub does.
    """

    def __init__(
        self,
        candidate: Candidate,
        *,
        job_input: bytes = b"OK",
        kind: str = "audio",
        snapshot: dict[str, Any] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> None:
        self.candidate = candidate
        self.job_input = job_input
        self.snapshot = snapshot if snapshot is not None else snapshot_for(candidate, kind=kind)
        self.postprocess_document: bytes | None = None
        if profile is not None:
            document = json.dumps(profile, sort_keys=True).encode("utf-8")
            self.postprocess_document = document
            self.snapshot = {
                **self.snapshot,
                "postprocess": {
                    "name": profile.get("name", "profile"),
                    "revision": 1,
                    "sha256": hashlib.sha256(document).hexdigest(),
                },
            }
        self.kind = kind
        self.approved = True
        self.approved_private = True
        self.job_heartbeat_status = 200
        self.policy_status = 200
        self.policy_body: dict[str, Any] = {"allowed": True}
        self.result_status = 200
        self.claimed = False
        self.claim_count = 0
        self.calls: list[tuple[str, str, httpx.Headers, bytes]] = []
        self.heartbeat_bodies: list[dict[str, Any]] = []

    def job_payload(self) -> dict[str, Any]:
        return {
            "job_id": 7,
            "kind": self.kind,
            "lease_token": "lease-abc",
            "lease_until": _iso(timedelta(seconds=120)),
            "attempt": 1,
            "input_sha256": hashlib.sha256(self.job_input).hexdigest(),
            "execution_snapshot": self.snapshot,
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def paths(self, suffix: str) -> list[tuple[str, str, httpx.Headers, bytes]]:
        return [c for c in self.calls if c[1].endswith(suffix)]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        path = request.url.path
        self.calls.append((request.method, path, request.headers, body))
        if path == "/v1/workers/heartbeat":
            self.heartbeat_bodies.append(json.loads(body))
            return httpx.Response(
                200,
                json={
                    "worker_id": "w1",
                    "approved": self.approved,
                    "approved_private": self.approved_private,
                    "approved_capabilities": [self.candidate.model_dump(mode="json")],
                    "server_time": _iso(timedelta(0)),
                },
            )
        if path == "/v1/workers/claim":
            self.claim_count += 1
            if self.claimed:
                return httpx.Response(204)
            self.claimed = True
            return httpx.Response(200, json=self.job_payload())
        if path.endswith("/input"):
            return httpx.Response(200, content=self.job_input)
        if path.endswith("/postprocess"):
            if self.postprocess_document is None:
                return httpx.Response(404, json={"error": "postprocess_not_found", "message": "none"})
            return httpx.Response(200, content=self.postprocess_document)
        if path.endswith("/heartbeat"):
            if self.job_heartbeat_status == 409:
                return httpx.Response(409, json={"error": "stale_lease", "message": "lease expired"})
            return httpx.Response(200, json={"lease_until": _iso(timedelta(seconds=120)), "cancel_requested": False})
        if path.endswith("/policy"):
            return httpx.Response(self.policy_status, json=self.policy_body)
        if path.endswith("/result"):
            if self.result_status != 200:
                return httpx.Response(self.result_status, json={"error": "stale_lease", "message": "lease expired"})
            return httpx.Response(200, json={"status": "completed"})
        if path.endswith("/fail"):
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(404, json={"error": "not_found", "message": "no route"})


@pytest.fixture
def token() -> str:
    return TOKEN


@pytest.fixture
def candidate() -> Candidate:
    return Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")


@pytest.fixture
def executor_script(tmp_path: Path) -> Path:
    path = tmp_path / "fake_exec.py"
    path.write_text(EXECUTOR_SCRIPT, encoding="utf-8")
    return path


@pytest.fixture
def build_supervisor(tmp_path: Path, executor_script: Path):
    def build(
        hub: FakeHub,
        *,
        capabilities: tuple[Candidate, ...],
        lease_heartbeat_seconds: float = 30.0,
    ) -> Supervisor:
        config = WorkerConfig(
            hub_url=HUB,
            token=TOKEN,
            work_dir=tmp_path / "work",
            capabilities=capabilities,
            executor=(sys.executable, str(executor_script)),
            lease_heartbeat_seconds=lease_heartbeat_seconds,
            claim_wait_seconds=0,
            poll_interval_seconds=0.01,
        )
        client = HubClient(HUB, TOKEN, transport=hub.transport(), sleep=lambda seconds: None)
        return Supervisor(config, hub=client, sleep=lambda seconds: None)

    return build


@pytest.fixture
def fakes() -> SimpleNamespace:
    """Test doubles exposed as a fixture (test modules cannot import conftest under importlib mode)."""
    return SimpleNamespace(FakeHub=FakeHub, snapshot_for=snapshot_for)
