"""Tests for the Deepgram-compatible prerecorded adapter ``POST /v1/listen``."""
import hashlib
import io
import json
import threading
import time
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from resona_job_core import Candidate
from sqlmodel import Session

from resona_api import audio_routes, listen_prerecorded
from resona_api.admin import approve_worker, enroll_worker
from resona_api.audio_routes import router as audio_router
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus
from resona_api.jobs_routes import router as jobs_router
from resona_api.listen_prerecorded import router as listen_router
from resona_api.releases import hub_release_id
from resona_api.worker_routes import router as worker_router
from release_approval import approve_release

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")


@pytest.fixture(scope="module")
def listen_app():
    app = FastAPI()
    for router in (jobs_router, worker_router, audio_router, listen_router):
        app.include_router(router)
    return app


@pytest.fixture
def http(listen_app, fresh_org, client_headers):
    with TestClient(listen_app, headers=client_headers(fresh_org)) as c:
        yield c


@pytest.fixture
def sync_wait(monkeypatch):
    def set_wait(seconds: float) -> None:
        monkeypatch.setattr(audio_routes, "SYNC_WAIT_SECONDS", seconds)
    set_wait(10.0)
    return set_wait


def _error(resp) -> dict:
    body = resp.json()
    return body["detail"] if "detail" in body else body


def _worker(org_id) -> dict[str, str]:
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "listen-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[MAC_AUDIO.model_dump(mode="json")]).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def _post_in_background(http, query: str, body: bytes):
    holder: dict = {}

    def run():
        holder["resp"] = http.post(f"/v1/listen{query}", content=body,
                                   headers={"Content-Type": "application/octet-stream"})

    thread = threading.Thread(target=run)
    thread.start()
    return thread, holder


def _claim_when_queued(http, worker, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = http.post("/v1/workers/claim?wait=0",
                         json={"capabilities": [MAC_AUDIO.model_dump(mode="json")]}, headers=worker)
        if resp.status_code == 200:
            return resp.json()
        time.sleep(0.05)
    raise AssertionError("job never became claimable")


def _complete(http, worker, lease, *, markdown, segments=(), language="de"):
    body = json.dumps({
        "schema_version": 1, "kind": "audio", "raw_text": markdown, "markdown": markdown,
        "language": language, "segments": list(segments),
        "provenance": {"stages": [{
            "stage": "asr", "engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx",
            "accelerator": "metal", "seconds": 0.5,
        }]},
    }).encode()
    resp = http.post(f"/v1/workers/jobs/{lease['job_id']}/result", content=body, headers={
        **worker, "X-Resona-Lease": lease["lease_token"], "Content-Type": "application/json",
        "X-Resona-Result-Sha256": hashlib.sha256(body).hexdigest(),
    })
    assert resp.status_code == 200, resp.text


def _input(http, worker, lease) -> bytes:
    resp = http.get(f"/v1/workers/jobs/{lease['job_id']}/input",
                    headers={**worker, "X-Resona-Lease": lease["lease_token"]})
    assert resp.status_code == 200, resp.text
    return resp.content


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_wav_header_wraps_pcm_into_a_readable_wav():
    pcm = b"\x01\x00\xff\xff" * 80
    data = listen_prerecorded.wav_header(len(pcm), sample_rate=8000, channels=2) + pcm
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 8000, 80)
        assert w.readframes(80) == pcm


def test_model_selects_an_engine_only_when_the_org_can_use_it(monkeypatch, fresh_org):
    monkeypatch.setattr(audio_routes, "_org_engines", lambda session, org: {"engines": [
        {"name": "mlx-whisper", "job_kind": "audio"}, {"name": "docling", "job_kind": "document"},
    ]})
    assert listen_prerecorded.model_engine(fresh_org, "mlx-whisper") == "mlx-whisper"
    assert listen_prerecorded.model_engine(fresh_org, "nova-3") is None
    assert listen_prerecorded.model_engine(fresh_org, "docling") is None
    assert listen_prerecorded.model_engine(fresh_org, None) is None


# ── The route ────────────────────────────────────────────────────────────────

def test_listen_returns_the_deepgram_shape(http, fresh_org, wav_bytes, sync_wait):
    worker = _worker(fresh_org)
    # Deepgram parameters the hub does not use are accepted and ignored; nova-3 means the default.
    thread, holder = _post_in_background(
        http, "?model=nova-3&smart_format=true&punctuate=true&keyterm=Olanzapin&keyterm=Quetiapin"
              "&detect_language=true", wav_bytes)
    lease = _claim_when_queued(http, worker)
    assert lease["execution_snapshot"]["policy"]["private"] is True
    assert _input(http, worker, lease) == wav_bytes
    segments = [{"start": 0.0, "end": 1.5, "text": " Hallo", "speaker": 1},
                {"start": 1.5, "end": 2.25, "text": " Welt", "speaker": 2}]
    _complete(http, worker, lease, markdown="Hallo Welt.", segments=segments)
    thread.join(timeout=10)

    resp = holder["resp"]
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "metadata": {
            "request_id": str(lease["job_id"]),
            "duration": 2.25,
            "channels": 1,
            "model_info": {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx"},
        },
        "results": {
            "channels": [{
                "detected_language": "de",
                "alternatives": [{"transcript": "Hallo Welt.", "confidence": None, "words": []}],
            }],
            "utterances": [
                {"start": 0.0, "end": 1.5, "transcript": "Hallo", "channel": 0, "speaker": 0},
                {"start": 1.5, "end": 2.25, "transcript": "Welt", "channel": 0, "speaker": 1},
            ],
        },
    }


def test_listen_wraps_linear16_pcm_into_wav(http, fresh_org, sync_wait):
    worker = _worker(fresh_org)
    pcm = b"\x10\x00\xf0\xff" * 400
    thread, holder = _post_in_background(http, "?encoding=linear16&sample_rate=8000", pcm)
    lease = _claim_when_queued(http, worker)
    stored = _input(http, worker, lease)
    with wave.open(io.BytesIO(stored)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, 8000, 800)
        assert w.readframes(800) == pcm
    _complete(http, worker, lease, markdown="")
    thread.join(timeout=10)
    assert holder["resp"].status_code == 200


def test_listen_times_out_with_job_location(http, wav_bytes, sync_wait):
    sync_wait(0.3)
    resp = http.post("/v1/listen", content=wav_bytes)
    assert resp.status_code == 504
    body = resp.json()
    assert body["error"] == "timeout"
    assert resp.headers["Location"] == f"/v1/jobs/{body['job_id']}"
    assert http.get(f"/v1/jobs/{body['job_id']}").json()["status"] == "pending"


def test_listen_idempotency_key_replays_the_job(http, wav_bytes, sync_wait):
    sync_wait(0.1)
    headers = {"Idempotency-Key": "listen-replay"}
    first = http.post("/v1/listen?language=en", content=wav_bytes, headers=headers)
    second = http.post("/v1/listen?language=en", content=wav_bytes, headers=headers)
    assert first.status_code == second.status_code == 504
    assert first.json()["job_id"] == second.json()["job_id"]
    with Session(engine) as session:
        assert session.get(Job, first.json()["job_id"]).status == JobStatus.PENDING


def test_listen_engine_override_goes_through_admission(http, wav_bytes, sync_wait):
    resp = http.post("/v1/listen?engine=deepgram", content=wav_bytes)
    assert resp.status_code == 422
    assert _error(resp)["error"] == "unapproved_override"


def test_listen_cloud_request_is_policy_denied(http, wav_bytes, sync_wait):
    resp = http.post("/v1/listen?private=false", content=wav_bytes)
    assert resp.status_code == 403
    assert _error(resp)["error"] == "policy_denied"


@pytest.mark.parametrize("query, body, status, code", [
    ("", b"", 422, "empty_upload"),
    ("", b"%PDF-1.7 not audio", 415, "unsupported_media"),
    ("", b"\x00\x01" * 64, 415, "unsupported_media"),
    ("?encoding=opus", b"OggS....", 422, "invalid_request"),
    ("?encoding=linear16", b"\x00\x01\x02", 422, "invalid_upload"),
])
def test_listen_rejects_bad_bodies(http, query, body, status, code):
    resp = http.post(f"/v1/listen{query}", content=body)
    assert resp.status_code == status
    assert _error(resp)["error"] == code


def test_listen_requires_a_client_credential(listen_app, wav_bytes):
    with TestClient(listen_app) as c:
        assert c.post("/v1/listen", content=wav_bytes).status_code == 401
