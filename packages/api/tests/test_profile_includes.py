"""Profile composition on the hub: includes resolve at admission, writes keep them resolvable."""

import asyncio
import hashlib
import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Candidate
from resona_postprocess.compose import bundled_lookup, flatten
from resona_postprocess.profile import Profile
from resona_api import audio_routes, storage
from resona_api import engine_registry as reg
from resona_api import streaming_routes as sr
from resona_api.admin import approve_worker, enroll_worker
from resona_api.audio_routes import router as audio_router
from resona_api.db.engine import engine
from resona_api.db.models import Job, Organization
from resona_api.jobs_routes import router as jobs_router
from resona_api.listen_prerecorded import router as listen_router
from resona_api.profiles_routes import router as profiles_router
from resona_api.releases import hub_release_id
from resona_api.worker_routes import router as worker_router
from release_approval import approve_release


class _FakeEngineWS:
    """A local engine-server ``/ws/live`` socket that answers ``stop`` with one final and ``stopped``."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send(self, msg):
        if json.loads(msg)["type"] == "stop":
            await self._queue.put(json.dumps({"type": "final", "text": "hallo", "delta": "hallo"}))
            await self._queue.put(json.dumps({"type": "stopped"}))
            await self._queue.put(None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _FakeConnect:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *exc):
        return False


def _local() -> reg.EngineInfo:
    return reg.EngineInfo(name="faster-whisper", kind="local", capabilities=["stt"], private=True,
                          available=True, models=["large-v3"], url="http://e:7001")


MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")

NAMES = {"name": "names", "block": True, "initial_prompt": ["Olanzapin"], "steps": [
    {"type": "replacements", "name": "names", "rules": [{"pattern": "Monique", "replacement": "Monic"}]},
]}
COMPOSED = {"name": "composed", "initial_prompt": ["Befund"], "steps": [
    {"type": "include", "profile": "names"},
    {"type": "include", "profile": "dictation-commands-de"},
]}


@pytest.fixture(scope="module")
def includes_app():
    app = FastAPI()
    for router in (jobs_router, worker_router, audio_router, profiles_router, listen_router):
        app.include_router(router)
    return app


@pytest.fixture
def http(includes_app):
    with TestClient(includes_app) as c:
        yield c


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def error_message(response) -> str:
    body = response.json()
    return body.get("message") or body["detail"]["message"]


def put(http, headers, name, body):
    return http.put(f"/profiles/{name}", json=body, headers=headers)


def submit_audio(http, headers, wav_bytes, **fields) -> int:
    upload_id = http.post("/v1/uploads", files={"file": ("a.wav", wav_bytes, "audio/wav")},
                          headers=headers).json()["upload_id"]
    resp = http.post("/v1/jobs", json={"kind": "audio", "upload_id": upload_id, **fields},
                     headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def snapshot(job_id) -> dict:
    with Session(engine) as session:
        return json.loads(session.get(Job, job_id).execution_snapshot)


def pinned_document(org_id, job_id) -> bytes:
    with Session(engine) as session:
        storage_id = session.get(Organization, org_id).storage_id
    return storage.postprocess_path(storage_id, job_id).read_bytes()


def make_worker(org_id) -> dict[str, str]:
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "includes-worker", org_id)
        approve_worker(session, worker_id, private=True,
                       capabilities=[MAC_AUDIO.model_dump(mode="json")]).runtime_version = hub_release_id()
        session.commit()
    approve_release(org_id)
    return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def bundled_rules(name: str) -> list[dict]:
    """The steps the bundled block ``name`` contributes when included: rules inline, no ``source``."""
    wrapper = Profile.from_dict({"name": "wrapper", "steps": [{"type": "include", "profile": name}]})
    return flatten(wrapper, bundled_lookup).steps


@pytest.fixture
def composed_org(fresh_org, admin_headers, http):
    admin = admin_headers(fresh_org)
    assert put(http, admin, "names", NAMES).status_code == 200
    assert put(http, admin, "composed", COMPOSED).status_code == 200
    return fresh_org


# ── Admission ────────────────────────────────────────────────────────────────


def test_worker_downloads_a_flat_document_and_the_snapshot_names_the_includes(
        http, composed_org, client_headers, wav_bytes):
    job_id = submit_audio(http, client_headers(composed_org), wav_bytes, profile="composed")
    meta = snapshot(job_id)["postprocess"]
    assert meta["name"] == "composed" and meta["revision"] == 1
    assert meta["includes"] == [
        {"name": "names", "revision": 1},
        {"name": "dictation-commands-de", "revision": 0},
    ]

    worker = make_worker(composed_org)
    claimed = http.post("/v1/workers/claim?wait=0", headers=worker,
                        json={"capabilities": [MAC_AUDIO.model_dump(mode="json")]}).json()
    assert claimed["job_id"] == job_id
    assert claimed["execution_snapshot"]["postprocess"]["includes"] == meta["includes"]
    resp = http.get(f"/v1/workers/jobs/{job_id}/postprocess",
                    headers={**worker, "X-Resona-Lease": claimed["lease_token"]})
    assert resp.status_code == 200
    assert hashlib.sha256(resp.content).hexdigest() == meta["sha256"]
    document = json.loads(resp.content)
    assert [step["type"] for step in document["steps"]] == ["replacements"] * (1 + len(bundled_rules(
        "dictation-commands-de")))
    assert document["steps"][0] == NAMES["steps"][0]
    assert document["steps"][1:] == bundled_rules("dictation-commands-de")
    assert all("source" not in step for step in document["steps"])
    assert document["initial_prompt"] == ["Befund", "Olanzapin"]
    assert "block" not in document


def test_bundled_default_is_pinned_flat_with_its_block(http, fresh_org, client_headers, wav_bytes):
    job_id = submit_audio(http, client_headers(fresh_org), wav_bytes, profile="default")
    assert snapshot(job_id)["postprocess"]["includes"] == [{"name": "dictation-commands-de", "revision": 0}]
    document = json.loads(pinned_document(fresh_org, job_id))
    assert document["steps"] == bundled_rules("dictation-commands-de")


def test_an_org_profile_shadows_the_bundled_block_of_that_name(
        http, fresh_org, admin_headers, client_headers, wav_bytes):
    admin = admin_headers(fresh_org)
    own = {"name": "dictation-commands-de", "steps": [
        {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ";"}]},
    ]}
    assert put(http, admin, "dictation-commands-de", own).status_code == 200
    assert put(http, admin, "composed", {"name": "composed", "steps": [
        {"type": "include", "profile": "dictation-commands-de"},
    ]}).status_code == 200
    job_id = submit_audio(http, client_headers(fresh_org), wav_bytes, profile="composed")
    assert snapshot(job_id)["postprocess"]["includes"] == [{"name": "dictation-commands-de", "revision": 1}]
    assert json.loads(pinned_document(fresh_org, job_id))["steps"] == own["steps"]


def test_a_profile_without_includes_carries_no_includes_meta(
        http, fresh_org, admin_headers, client_headers, wav_bytes):
    assert put(http, admin_headers(fresh_org), "names", NAMES).status_code == 200
    job_id = submit_audio(http, client_headers(fresh_org), wav_bytes, profile="names")
    assert "includes" not in snapshot(job_id)["postprocess"]


def test_an_unresolvable_stored_include_is_refused_at_admission(
        http, fresh_org, store_profile, client_headers, wav_bytes):
    # Written around the PUT checks (a profile stored by an older hub).
    store_profile(fresh_org, "broken", {"name": "broken", "steps": [{"type": "include", "profile": "gone"}]})
    headers = client_headers(fresh_org)
    upload_id = http.post("/v1/uploads", files={"file": ("a.wav", wav_bytes, "audio/wav")},
                          headers=headers).json()["upload_id"]
    resp = http.post("/v1/jobs", json={"kind": "audio", "upload_id": upload_id, "profile": "broken"},
                     headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"
    assert "gone" in error_message(resp)


def test_an_included_profile_reading_a_source_is_refused_at_admission(
        http, fresh_org, store_profile, client_headers, wav_bytes):
    # Hosted profiles keep refusing ``source``, also when they are only included.
    store_profile(fresh_org, "reader", {"name": "reader", "steps": [
        {"type": "replacements", "source": "/etc/passwd"},
    ]})
    store_profile(fresh_org, "outer", {"name": "outer", "steps": [{"type": "include", "profile": "reader"}]})
    headers = client_headers(fresh_org)
    upload_id = http.post("/v1/uploads", files={"file": ("a.wav", wav_bytes, "audio/wav")},
                          headers=headers).json()["upload_id"]
    resp = http.post("/v1/jobs", json={"kind": "audio", "upload_id": upload_id, "profile": "outer"},
                     headers={**headers, "Idempotency-Key": uuid.uuid4().hex})
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"


@pytest.mark.parametrize("path", ["/v1/listen?profile=composed", "/v1/audio/transcriptions"])
def test_sync_adapters_pin_the_flat_document(http, composed_org, client_headers, wav_bytes, monkeypatch, path):
    monkeypatch.setattr(audio_routes, "SYNC_WAIT_SECONDS", 0.2)
    headers = client_headers(composed_org)
    if path.startswith("/v1/listen"):
        resp = http.post(path, content=wav_bytes, headers=headers)
    else:
        resp = http.post(path, files={"file": ("a.wav", wav_bytes, "audio/wav")},
                         data={"profile": "composed"}, headers=headers)
    assert resp.status_code == 504, resp.text
    job_id = (resp.json().get("job_id") or resp.json()["detail"]["job_id"])
    assert [i["name"] for i in snapshot(job_id)["postprocess"]["includes"]] == ["names", "dictation-commands-de"]
    assert "include" not in {step["type"] for step in json.loads(pinned_document(composed_org, job_id))["steps"]}


def test_live_stream_admission_uses_the_flat_profile(monkeypatch, composed_org, client_headers):
    fake_ws = _FakeEngineWS()
    urls = []

    def connect(url, **kwargs):
        urls.append(url)
        return _FakeConnect(fake_ws)

    monkeypatch.setattr(sr.websockets, "connect", connect)
    monkeypatch.setattr(reg, "resolve", lambda *a, **k: _local())
    monkeypatch.setattr(reg, "get_catalogue", lambda: [_local()])
    app = FastAPI()
    app.include_router(sr.router)
    with TestClient(app) as c:
        c.headers.update(client_headers(composed_org))
        with c.websocket_connect("/v1/listen?encoding=linear16&profile=composed") as ws:
            ws.send_text(json.dumps({"type": "CloseStream"}))
            messages = []
            while not messages or messages[-1]["type"] not in ("Metadata", "Error"):
                messages.append(ws.receive_json())
    assert messages[-1]["type"] == "Metadata"
    # The included block's initial_prompt reaches the engine: the profile was flattened.
    assert "Olanzapin" in urls[0]


# ── Writes ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", [
    {"name": "x", "steps": [{"type": "include", "profile": "missing-block"}]},
    {"name": "x", "steps": [{"type": "include", "profile": "x"}]},
])
def test_put_refuses_a_missing_or_self_include(http, fresh_org, admin_headers, body):
    resp = put(http, admin_headers(fresh_org), "x", body)
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"


def test_put_refuses_a_cycle_through_stored_profiles(http, fresh_org, admin_headers):
    admin = admin_headers(fresh_org)
    assert put(http, admin, "b", {"name": "b", "steps": []}).status_code == 200
    assert put(http, admin, "a", {"name": "a", "steps": [{"type": "include", "profile": "b"}]}).status_code == 200
    resp = put(http, admin, "b", {"name": "b", "steps": [{"type": "include", "profile": "a"}]})
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"
    assert "cycle" in error_message(resp)
    # The refused revision was not stored.
    assert http.get("/profiles/b", headers=admin).json()["revision"] == 1


def test_put_refuses_a_change_that_breaks_a_profile_including_it(http, fresh_org, admin_headers):
    admin = admin_headers(fresh_org)
    assert put(http, admin, "b", {"name": "b", "steps": []}).status_code == 200
    assert put(http, admin, "a", {"name": "a", "steps": [{"type": "include", "profile": "b"}]}).status_code == 200
    resp = put(http, admin, "b", {"name": "b", "instructions": "Be brief.", "steps": []})
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"
    assert "'a'" in error_message(resp)


def test_put_accepts_an_include_of_a_bundled_block(http, fresh_org, admin_headers):
    resp = put(http, admin_headers(fresh_org), "x", {"name": "x", "steps": [
        {"type": "include", "profile": "filler-words-de"},
    ]})
    assert resp.status_code == 200


def test_put_still_refuses_source(http, fresh_org, admin_headers):
    resp = put(http, admin_headers(fresh_org), "x", {"name": "x", "steps": [
        {"type": "replacements", "source": "rules.json"},
    ]})
    assert resp.status_code == 422
    assert error_code(resp) == "invalid_profile"


def test_delete_refuses_a_profile_another_one_includes(http, fresh_org, admin_headers):
    admin = admin_headers(fresh_org)
    assert put(http, admin, "b", {"name": "b", "steps": []}).status_code == 200
    assert put(http, admin, "a", {"name": "a", "steps": [{"type": "include", "profile": "b"}]}).status_code == 200
    assert put(http, admin, "c", {"name": "c", "steps": [{"type": "include", "profile": "a"}]}).status_code == 200

    resp = http.delete("/profiles/b", headers=admin)
    assert resp.status_code == 409
    assert error_code(resp) == "profile_in_use"
    assert error_message(resp) == "included by a, c"
    assert http.get("/profiles/b", headers=admin).status_code == 200

    assert http.delete("/profiles/a", headers=admin).status_code == 409
    assert http.delete("/profiles/c", headers=admin).status_code == 200
    assert http.delete("/profiles/a", headers=admin).status_code == 200
    assert http.delete("/profiles/b", headers=admin).status_code == 200


# ── Reads ────────────────────────────────────────────────────────────────────


def test_get_returns_the_stored_document_or_the_resolved_one(http, composed_org, client_headers):
    headers = client_headers(composed_org)
    stored = http.get("/profiles/composed", headers=headers).json()
    assert stored["profile"]["steps"] == COMPOSED["steps"]
    assert "includes" not in stored

    resolved = http.get("/profiles/composed?resolved=true", headers=headers).json()
    assert resolved["revision"] == 1
    assert resolved["includes"] == [
        {"name": "names", "revision": 1},
        {"name": "dictation-commands-de", "revision": 0},
    ]
    assert "include" not in {step["type"] for step in resolved["profile"]["steps"]}
    assert resolved["profile"]["initial_prompt"] == ["Befund", "Olanzapin"]


def test_get_keeps_the_block_flag(http, composed_org, client_headers):
    assert http.get("/profiles/names", headers=client_headers(composed_org)).json()["profile"]["block"] is True


def test_get_resolved_of_an_unresolvable_profile_is_409(http, fresh_org, store_profile, client_headers):
    store_profile(fresh_org, "broken", {"name": "broken", "steps": [{"type": "include", "profile": "gone"}]})
    headers = client_headers(fresh_org)
    assert http.get("/profiles/broken", headers=headers).status_code == 200
    resp = http.get("/profiles/broken?resolved=true", headers=headers)
    assert resp.status_code == 409
    assert error_code(resp) == "stored_profile_invalid"
