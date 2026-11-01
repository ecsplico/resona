"""Tests for the OpenAI-compatible /v1/audio/* + /v1/engines routes."""
import hashlib
import json
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from resona_job_core import Candidate, Endpoint, default_settings
from resona_api import audio_routes
from resona_api import engine_registry as reg
from resona_api.admin import approve_worker, enroll_worker
from resona_api.releases import hub_release_id
from release_approval import approve_release
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, Upload

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")


@pytest.fixture
def fresh_client(test_app, fresh_org, client_headers):
    """Client authenticated as a brand-new organization with default settings."""
    with TestClient(test_app, headers=client_headers(fresh_org)) as c:
        yield c


def _catalogue():
    return [
        reg.EngineInfo("faster-whisper", "local", ["stt"], True, True, ["large-v3"]),
        reg.EngineInfo("deepgram", "cloud", ["stt", "tts"], False, True,
                       ["nova-3"], provider="deepgram"),
    ]


DEEPGRAM = Candidate(engine="deepgram", model="nova-3", accelerator="cpu")


def _engine_worker(org_id, caps, *, reported=None, private=True, online=True) -> str:
    """An approved worker with ``caps`` approved and ``reported`` (default: caps) tested."""
    from resona_api import clock
    from resona_api.db.models import Worker

    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "engines-worker", org_id)
        approve_worker(session, worker_id, private=private, capabilities=[c.model_dump(mode="json") for c in caps])
        worker = session.get(Worker, worker_id)
        worker.reported_capabilities = json.dumps(
            [c.model_dump(mode="json") for c in (caps if reported is None else reported)])
        worker.last_seen = clock.now() if online else None
        session.add(worker)
        session.commit()
    return worker_id


def _engines(http) -> dict:
    with patch.object(reg, "speech_catalogue", return_value=[]), \
         patch.object(reg, "get_catalogue", side_effect=AssertionError("no engine-server probe")):
        resp = http.get("/v1/engines")
    assert resp.status_code == 200
    return resp.json()


def test_engines_without_workers_lists_nothing(fresh_client):
    assert _engines(fresh_client) == {"engines": [], "default": None}


def test_engines_lists_tested_approved_candidates_of_the_org(fresh_client, fresh_org, org_b):
    _engine_worker(fresh_org, [MAC_AUDIO])
    # Approved but never tested (prepared): not exposed.
    _engine_worker(fresh_org, [Candidate(engine="whisper", model="large-v3", accelerator="cuda")], reported=[])
    # Another organization's worker never leaks.
    _engine_worker(org_b, [Candidate(engine="faster-whisper", model="Systran/faster-whisper-large-v3",
                                     accelerator="cpu")])

    body = _engines(fresh_client)

    assert [e["name"] for e in body["engines"]] == ["mlx-whisper"]
    mlx = body["engines"][0]
    assert (mlx["kind"], mlx["job_kind"], mlx["private"], mlx["available"]) == ("local", "audio", True, True)
    assert mlx["capabilities"] == ["stt"]
    assert mlx["models"] == [MAC_AUDIO.model]
    assert "url" not in mlx
    [listed] = mlx["candidates"]
    assert listed["candidate"] == MAC_AUDIO.model_dump(mode="json")
    assert listed["execution_profiles"] == ["mac-private", "private-auto"]
    assert body["default"] == "mlx-whisper"


def test_engines_marks_offline_workers_unavailable(fresh_client, fresh_org):
    _engine_worker(fresh_org, [MAC_AUDIO], online=False)
    [mlx] = _engines(fresh_client)["engines"]
    assert mlx["available"] is False


def test_cloud_engines_only_when_org_allows_cloud(fresh_client, fresh_org, store_settings):
    profiles = {name: p.model_dump(mode="json") for name, p in default_settings().execution_profiles.items()}
    profiles["cloud"] = {"audio": [DEEPGRAM.model_dump(mode="json")], "document": []}
    endpoint = Endpoint(id="cloud-asr:deepgram", kind="cloud_asr", model="nova-3")
    store_settings(fresh_org, execution_profiles=profiles, endpoints=[endpoint.model_dump(mode="json")])
    _engine_worker(fresh_org, [DEEPGRAM], private=False)
    # A private worker never offers a cloud provider, even if approved for it.
    _engine_worker(fresh_org, [DEEPGRAM], private=True)

    assert _engines(fresh_client)["engines"] == []

    store_settings(fresh_org, cloud_enabled=True)
    [deepgram] = _engines(fresh_client)["engines"]
    assert (deepgram["name"], deepgram["kind"], deepgram["private"]) == ("deepgram", "cloud", False)
    assert deepgram["candidates"][0]["execution_profiles"] == ["cloud"]

    store_settings(fresh_org, endpoints=[])
    assert _engines(fresh_client)["engines"] == []


def test_speech_returns_audio_for_cloud_enabled_org(fresh_client, fresh_org, store_settings):
    store_settings(fresh_org, cloud_enabled=True)
    info = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_tts",
                       return_value={"audio": b"mp3bytes",
                                     "content_type": "audio/mpeg"}):
        resp = fresh_client.post(
            "/v1/audio/speech",
            json={"input": "hallo welt", "engine": "deepgram", "private": False},
        )
    assert resp.status_code == 200
    assert resp.content == b"mp3bytes"
    assert resp.headers["content-type"] == "audio/mpeg"


def test_speech_cloud_is_denied_for_private_default(fresh_client):
    info = _catalogue()[1]
    with patch.object(reg, "resolve", return_value=info), \
         patch.object(reg, "run_tts") as run_tts:
        resp = fresh_client.post(
            "/v1/audio/speech",
            json={"input": "hallo welt", "engine": "deepgram"},
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "policy_denied"
    run_tts.assert_not_called()


def test_speech_private_yields_409(client):
    err = reg.NoEngineError("no private engine available for tts")
    with patch.object(reg, "resolve", side_effect=err):
        resp = client.post(
            "/v1/audio/speech",
            json={"input": "geheim", "private": True},
        )
    assert resp.status_code == 409



# ── /v1/audio/transcriptions: adapter onto the job path ─────────────────────

@pytest.fixture
def org_http(test_app, fresh_org, client_headers):
    with TestClient(test_app, headers=client_headers(fresh_org)) as c:
        yield c


def _error(resp) -> dict:
    body = resp.json()
    return body["detail"] if "detail" in body else body


def _worker(org_id) -> dict[str, str]:
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "sync-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[MAC_AUDIO.model_dump(mode="json")]).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def _transcribe_in_background(http, data, wav_bytes):
    """POST the OpenAI request on a thread; returns (thread, holder) with holder['resp']."""
    holder: dict = {}

    def run():
        holder["resp"] = http.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_bytes, "audio/wav")},
            data=data,
        )

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


def _complete(http, worker, lease, *, markdown, raw_text, segments=(), structured=None):
    body = json.dumps({
        "schema_version": 1, "kind": "audio", "raw_text": raw_text, "markdown": markdown,
        "language": "de", "segments": list(segments), "structured": structured or {},
        "provenance": {"stages": []},
    }).encode()
    resp = http.post(f"/v1/workers/jobs/{lease['job_id']}/result", content=body, headers={
        **worker, "X-Resona-Lease": lease["lease_token"], "Content-Type": "application/json",
        "X-Resona-Result-Sha256": hashlib.sha256(body).hexdigest(),
    })
    assert resp.status_code == 200, resp.text


@pytest.fixture
def sync_wait(monkeypatch):
    def set_wait(seconds: float) -> None:
        monkeypatch.setattr(audio_routes, "SYNC_WAIT_SECONDS", seconds)
    set_wait(10.0)
    return set_wait


def test_transcription_returns_worker_result_from_queued_job(org_http, fresh_org, wav_bytes, sync_wait):
    worker = _worker(fresh_org)
    thread, holder = _transcribe_in_background(org_http, {"model": "whisper-1"}, wav_bytes)
    lease = _claim_when_queued(org_http, worker)
    assert lease["execution_snapshot"]["policy"]["private"] is True
    _complete(org_http, worker, lease, markdown="Hallo, Welt.", raw_text="hallo komma welt punkt")
    thread.join(timeout=10)

    resp = holder["resp"]
    assert resp.status_code == 200
    assert resp.json() == {"text": "Hallo, Welt."}
    with Session(engine) as session:
        assert session.get(Job, lease["job_id"]).status == JobStatus.COMPLETED


def test_transcription_verbose_json_and_text_formats(org_http, fresh_org, wav_bytes, sync_wait):
    worker = _worker(fresh_org)
    segments = [{"start": 0.0, "end": 1.5, "text": "Hallo"}, {"start": 1.5, "end": 2.25, "text": "Welt"}]
    thread, holder = _transcribe_in_background(org_http, {"response_format": "verbose_json"}, wav_bytes)
    lease = _claim_when_queued(org_http, worker)
    _complete(org_http, worker, lease, markdown="Hallo Welt", raw_text="Hallo Welt",
              segments=segments, structured={"summary": "x"})
    thread.join(timeout=10)
    body = holder["resp"].json()
    assert body["text"] == "Hallo Welt" and body["language"] == "de"
    assert body["duration"] == 2.25 and body["segments"] == segments
    assert body["structured"] == {"summary": "x"}

    thread, holder = _transcribe_in_background(org_http, {"response_format": "text"}, wav_bytes)
    lease = _claim_when_queued(org_http, worker)
    _complete(org_http, worker, lease, markdown="nur text", raw_text="nur text")
    thread.join(timeout=10)
    assert holder["resp"].text == "nur text"


def test_transcription_times_out_with_job_location_and_job_continues(org_http, wav_bytes, sync_wait):
    sync_wait(0.3)
    resp = org_http.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav_bytes, "audio/wav")})
    assert resp.status_code == 504
    body = resp.json()
    assert body["error"] == "timeout"
    job_id = body["job_id"]
    assert resp.headers["Location"] == f"/v1/jobs/{job_id}"
    view = org_http.get(f"/v1/jobs/{job_id}").json()
    assert view["status"] == "pending" and view["queue_reason"] == "no_capable_worker"


def test_transcription_uses_the_same_admission_as_jobs(org_http, fresh_org, wav_bytes, sync_wait):
    sync_wait(0.1)
    resp = org_http.post("/v1/audio/transcriptions",
                         files={"file": ("a.wav", wav_bytes, "audio/wav")},
                         data={"execution_profile": "cpu-private", "language": "en"})
    job_id = resp.json()["job_id"]
    with Session(engine) as session:
        snapshot = json.loads(session.get(Job, job_id).execution_snapshot)
    assert snapshot["execution_profile"] == "cpu-private"
    assert snapshot["language"] == "en"
    assert [c["engine"] for c in snapshot["candidates"]] == ["faster-whisper"]


def _unconsumed_uploads(org_id) -> list[Upload]:
    with Session(engine) as session:
        return session.exec(select(Upload).where(
            Upload.organization_id == org_id, Upload.consumed_by_job_id.is_(None))).all()


def test_transcription_cloud_request_is_policy_denied_and_upload_discarded(org_http, fresh_org, wav_bytes):
    resp = org_http.post("/v1/audio/transcriptions",
                         files={"file": ("a.wav", wav_bytes, "audio/wav")},
                         data={"private": "false"})
    assert resp.status_code == 403
    assert _error(resp)["error"] == "policy_denied"
    assert _unconsumed_uploads(fresh_org) == []


@pytest.mark.parametrize("data, code", [
    ({"engine": "deepgram"}, "unapproved_override"),
    ({"model": "large-v2"}, "unapproved_override"),
    ({"profile": '{"name":"t","steps":[]}'}, "invalid_profile"),
    ({"response_format": "srt"}, "invalid_request"),
])
def test_transcription_rejects_unapproved_or_invalid_requests(org_http, fresh_org, wav_bytes, data, code):
    resp = org_http.post("/v1/audio/transcriptions",
                         files={"file": ("a.wav", wav_bytes, "audio/wav")}, data=data)
    assert resp.status_code == 422
    assert _error(resp)["error"] == code
    assert _unconsumed_uploads(fresh_org) == []


def test_transcription_unknown_profile_is_404(org_http, wav_bytes):
    resp = org_http.post("/v1/audio/transcriptions",
                         files={"file": ("a.wav", wav_bytes, "audio/wav")}, data={"profile": "missing"})
    assert resp.status_code == 404
    assert _error(resp)["error"] == "profile_not_found"


def test_transcription_rejects_document_upload(org_http):
    resp = org_http.post("/v1/audio/transcriptions",
                         files={"file": ("a.pdf", b"%PDF-1.4\n", "application/pdf")})
    assert resp.status_code == 422
    assert _error(resp)["error"] == "kind_mismatch"


def test_transcription_reports_failed_job(org_http, fresh_org, wav_bytes, sync_wait):
    worker = _worker(fresh_org)
    thread, holder = _transcribe_in_background(org_http, {}, wav_bytes)
    lease = _claim_when_queued(org_http, worker)
    fail = org_http.post(f"/v1/workers/jobs/{lease['job_id']}/fail",
                         json={"code": "unsupported_input", "message": "cannot parse", "retryable": False},
                         headers={**worker, "X-Resona-Lease": lease["lease_token"]})
    assert fail.status_code == 200
    thread.join(timeout=10)
    resp = holder["resp"]
    assert resp.status_code == 502
    assert resp.json()["error"] == "unsupported_input" and resp.json()["job_id"] == lease["job_id"]
