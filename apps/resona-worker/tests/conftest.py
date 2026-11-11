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

if sys.argv[1:2] == ["--probe-accelerator"]:
    # Mirrors the real probe's contract: CUDA_VISIBLE_DEVICES="" hides every GPU (exit 4).
    hidden = sys.argv[2] == "cuda" and os.environ.get("CUDA_VISIBLE_DEVICES") == ""
    sys.exit(4 if hidden else 0)

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
if data == b"METALOOM":
    sys.stderr.write(
        "libc++abi: terminating due to uncaught exception of type std::runtime_error: "
        "[metal::malloc] Attempting to allocate 68719476736 bytes which is greater than the maximum allowed buffer size\n"
    )
    sys.stderr.flush()
    sys.exit(1)  # a native abort exits non-zero; only the stderr marker identifies OOM
if data == b"ENV":
    text = json.dumps(dict(os.environ))
elif data.startswith((b"%PDF-", b"PK\x03\x04")):
    text = "suffix=" + Path(args.input).suffix
else:
    text = "hallo welt"
snapshot = json.loads(Path(args.snapshot).read_text())
cand = snapshot["candidate"]
result = {
    "schema_version": 1,
    "kind": snapshot["kind"],
    "raw_text": text,
    "markdown": text,
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
        self.approved_capabilities = [candidate]
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
                    "approved_capabilities": [c.model_dump(mode="json") for c in self.approved_capabilities],
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
        endpoint_transport: httpx.BaseTransport | None = None,
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
        return Supervisor(
            config, hub=client, sleep=lambda seconds: None, endpoint_transport=endpoint_transport
        )

    return build


@pytest.fixture
def fakes() -> SimpleNamespace:
    """Test doubles exposed as a fixture (test modules cannot import conftest under importlib mode)."""
    return SimpleNamespace(FakeHub=FakeHub, snapshot_for=snapshot_for, manifest_json=manifest_json)


def manifest_json(candidates: list[Candidate], *, profile: str = "cpu-private", kind: str = "audio") -> str:
    """A capabilities manifest as `resona-worker prepare` writes it."""
    return json.dumps(
        {
            "schema_version": 1,
            "profile": profile,
            "prepared_at": "2026-10-31T12:00:00Z",
            "runtime_version": "0.1.0",
            "platform": "darwin-arm64",
            "tested": [
                {"kind": kind, "candidate": c.model_dump(mode="json"), "seconds": 1.0, "peak_rss_bytes": 1}
                for c in candidates
            ],
        }
    )


PREPARE_EXECUTOR_SCRIPT = r'''
import argparse, json, os, sys
from pathlib import Path

from resona_worker.weights import is_hub_repo, local_snapshot


def loaded(model, revision):
    """Like the real executor: HF repos load the cached snapshot, provenance names its commit."""
    if not is_hub_repo(model):
        return revision
    snapshot = local_snapshot(model, revision)
    return snapshot.name if snapshot is not None else revision


parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True)
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--metrics", required=True)
args = parser.parse_args()
snapshot = json.loads(Path(args.snapshot).read_text())
cand = snapshot["candidate"]
if "broken" in cand["model"]:
    sys.exit(3)
device = "cpu" if "wrongdevice" in cand["model"] else cand["accelerator"]
if snapshot["kind"] == "audio":
    text = "Guten Tag, das ist ein Test."
    stages = [{"stage": "asr", "engine": cand["engine"], "model": cand["model"],
               "model_revision": loaded(cand["model"], cand["model_revision"]), "accelerator": device, "seconds": 0.1}]
elif Path(args.input).suffix == ".docx":
    if "noword" in cand["model"]:
        sys.exit(3)
    text = "## Synthetischer Befund\n\nDie Straße führt über die Brücke.\n\n- Erster Punkt\n\n| Temperatur | normal |"
    stages = [{"stage": "layout", "engine": "docling-msword", "model": "docling-msword-backend", "accelerator": "cpu",
               "seconds": 0.01}]
else:
    text = "Dies ist ein synthetisches Testdokument. Die Straße führt über die Brücke."
    layout = "docling-project/docling-layout-heron"
    stages = [{"stage": "layout", "engine": "docling-layout", "model": layout, "model_revision": loaded(layout, "main"),
               "accelerator": device, "seconds": 0.1},
              {"stage": "ocr", "engine": cand["ocr_engine"], "model": "apple-vision", "accelerator": "metal", "seconds": 0.1}]
if "garbage" in cand["model"]:
    text = "lorem ipsum"
structured = {"env": sorted(os.environ), "input_suffix": Path(args.input).suffix}
result = {"kind": snapshot["kind"], "raw_text": text, "markdown": text, "provenance": {"stages": stages},
          "structured": structured, "language": snapshot["language"]}
Path(args.out).write_text(json.dumps(result))
Path(args.metrics).write_text(json.dumps({"peak_accelerator_bytes": 123456}))
'''


@pytest.fixture
def prepare_executor(tmp_path: Path) -> Path:
    """Stub execution subprocess for ``prepare``; behaviour is selected by the candidate model name."""
    path = tmp_path / "fake_prepare_exec.py"
    path.write_text(PREPARE_EXECUTOR_SCRIPT, encoding="utf-8")
    return path


@pytest.fixture
def hf_cache(tmp_path: Path, monkeypatch):
    """An empty HuggingFace hub cache (``HF_HUB_CACHE``); ``add(repo, commit, *refs)`` caches a snapshot."""
    root = tmp_path / "hf-cache"
    root.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(root))
    monkeypatch.delenv("RESONA_MODELS_DIR", raising=False)
    monkeypatch.delenv("VOICEBOX_MODELS_DIR", raising=False)

    def add(repo: str, commit: str, *refs: str) -> Path:
        folder = root / f"models--{repo.replace('/', '--')}"
        snapshot = folder / "snapshots" / commit
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "config.json").write_text("{}", encoding="utf-8")
        for ref in refs:
            (folder / "refs").mkdir(exist_ok=True)
            (folder / "refs" / ref).write_text(commit, encoding="utf-8")
        return snapshot

    return SimpleNamespace(root=root, add=add)
