"""Testbench: contribute recordings/scans, draft run, reference, bench runs, leaderboard.

Jobs are driven through the real worker routes (heartbeat -> claim -> result), so the
tests cover admission, claiming without a promoted release, result capture and scoring.
Time is injected through ``clock.now`` like in ``test_retention``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from resona_job_core import Candidate
from resona_api import broker, clock, jobs_routes, maintenance, storage
from resona_api.admin import approve_worker, enroll_worker, revoke_worker
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, Organization, TestbenchItem, TestbenchRun

CPU_AUDIO = Candidate(engine="faster-whisper", model="Systran/faster-whisper-large-v3", accelerator="cpu")
MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
CUDA_AUDIO = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
CPU_DOC = Candidate(engine="docling", model="docling-standard", accelerator="cpu", ocr_engine="rapidocr")
MAC_DOC = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
CLOUD = Candidate(engine="deepgram", model="nova-3", accelerator="cpu")
PDF = b"%PDF-1.4\n% synthetic scan\n"
T0 = datetime(2031, 3, 1, 8, 0, tzinfo=UTC)
BASE = "/v1/testbench"


def dump(candidate: Candidate) -> dict:
    return candidate.model_dump(mode="json")


# -- fixtures and helpers -------------------------------------------------

@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def at(monkeypatch):
    def set_time(when: datetime) -> None:
        monkeypatch.setattr(clock, "now", lambda: when)

    set_time(T0)
    return set_time


@pytest.fixture
def bench_org(fresh_org, store_settings) -> str:
    """An organization that enabled the testbench."""
    store_settings(fresh_org, testbench_enabled=True)
    return fresh_org


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


def error_message(response) -> str:
    body = response.json()
    return (body.get("message") or body["detail"]["message"]).lower()


def storage_id(org_id: str) -> str:
    with Session(engine) as session:
        return session.get(Organization, org_id).storage_id


def online_worker(http, org_id, reported, *, approved=None, private=True):
    """Enroll + approve a worker and heartbeat the ``reported`` (tested) tuples.

    The worker runs release ``0.1.0``, which the hub never promoted: testbench jobs must
    not need a release approval.
    """
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "bench-worker", org_id)
        approve_worker(
            session, worker_id, private=private,
            capabilities=[dump(c) for c in (approved if approved is not None else reported)],
        )
        session.commit()
    headers = {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}
    beat = http.post("/v1/workers/heartbeat", headers=headers, json={
        "worker_protocol": 1, "runtime_version": "0.1.0", "ready": True,
        "reported_capabilities": [dump(c) for c in reported],
    })
    assert beat.status_code == 200, beat.text
    return worker_id, headers


def claim(http, worker, caps):
    return http.post("/v1/workers/claim?wait=0", json={"capabilities": [dump(c) for c in caps]}, headers=worker)


def result_body(text, *, kind="audio", seconds=1.0, accelerator="cpu", markdown=None, warnings=()):
    stage = "asr" if kind == "audio" else "layout"
    return {
        "schema_version": 1, "kind": kind, "raw_text": text,
        "markdown": text if markdown is None else markdown, "warnings": list(warnings),
        "provenance": {"stages": [{
            "stage": stage, "engine": "engine", "model": "model", "accelerator": accelerator,
            "seconds": seconds,
        }]},
    }


def post_result(http, job_id, worker, lease, payload):
    body = json.dumps(payload).encode()
    return http.post(f"/v1/workers/jobs/{job_id}/result", content=body, headers={
        **worker, "X-Resona-Lease": lease, "Content-Type": "application/json",
        "X-Resona-Result-Sha256": hashlib.sha256(body).hexdigest(),
    })


def finish_next(http, worker, caps, text, **kwargs) -> dict:
    """Claim the oldest eligible job and complete it with ``text``; return the claim."""
    granted = claim(http, worker, caps)
    assert granted.status_code == 200, granted.text
    claimed = granted.json()
    posted = post_result(http, claimed["job_id"], worker, claimed["lease_token"], result_body(text, **kwargs))
    assert posted.status_code == 200, posted.text
    return claimed


def post_item(http, headers, data, *, name="memo.wav", ctype="audio/wav", **form):
    return http.post(f"{BASE}/items", headers=headers, files={"file": (name, data, ctype)}, data=form)


def contribute(http, headers, data, **kwargs) -> dict:
    created = post_item(http, headers, data, **kwargs)
    assert created.status_code == 201, created.text
    return created.json()


def get_item(http, headers, item_id) -> dict:
    resp = http.get(f"{BASE}/items/{item_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def put_reference(http, headers, item_id, text) -> dict:
    resp = http.put(f"{BASE}/items/{item_id}/reference", json={"text": text}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def run_job_id(run_id: int) -> int:
    with Session(engine) as session:
        return session.get(TestbenchRun, run_id).job_id


def job_row(job_id: int) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def item_path(org_id, item_id):
    return storage.testbench_item_path(storage_id(org_id), item_id)


def run_path(org_id, run_id):
    return storage.testbench_run_path(storage_id(org_id), run_id)


# -- gate -----------------------------------------------------------------

def test_status_answers_when_disabled_and_everything_else_is_403(
    http, fresh_org, admin_headers, client_headers, wav_bytes
):
    admin, client = admin_headers(fresh_org), client_headers(fresh_org)
    for headers in (admin, client):
        resp = http.get(f"{BASE}/status", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"enabled": False, "items": 0, "corrected": 0, "runs": 0}

    probes = [
        http.get(f"{BASE}/candidates", headers=admin),
        http.get(f"{BASE}/items", headers=admin),
        http.get(f"{BASE}/items/1", headers=admin),
        http.get(f"{BASE}/items/1/file", headers=admin),
        http.put(f"{BASE}/items/1/reference", json={"text": "x"}, headers=admin),
        http.delete(f"{BASE}/items/1", headers=admin),
        http.post(f"{BASE}/items/1/runs", json={"candidates": [dump(CPU_AUDIO)]}, headers=admin),
        http.get(f"{BASE}/items/1/runs/1", headers=admin),
        http.delete(f"{BASE}/items/1/runs/1", headers=admin),
        http.get(f"{BASE}/leaderboard", headers=admin),
        post_item(http, client, wav_bytes),
        post_item(http, admin, wav_bytes),
    ]
    for resp in probes:
        assert resp.status_code == 403, resp.request.url
        assert error_code(resp) == "testbench_disabled"
    message = error_message(probes[0])
    assert "admin ui" in message and "testbench_enabled" in message
    assert "resona settings" not in message


def test_org_admin_enables_the_testbench_through_settings(http, fresh_org, admin_headers, client_headers, wav_bytes):
    admin, client = admin_headers(fresh_org), client_headers(fresh_org)
    current = http.get("/v1/settings", headers=admin)
    settings = current.json()["settings"]
    assert settings["testbench_enabled"] is False
    assert post_item(http, client, wav_bytes).status_code == 403

    settings["testbench_enabled"] = True
    saved = http.put("/v1/settings", json=settings, headers={**admin, "If-Match": current.headers["ETag"]})
    assert saved.status_code == 200, saved.text

    assert http.get(f"{BASE}/status", headers=client).json()["enabled"] is True
    assert post_item(http, client, wav_bytes).status_code == 201


# -- roles and tenancy ----------------------------------------------------

def test_role_matrix(http, bench_org, admin_headers, client_headers, operator_headers, wav_bytes):
    admin, client, operator = admin_headers(bench_org), client_headers(bench_org), operator_headers(bench_org)
    item = contribute(http, admin, wav_bytes)
    item_id = item["id"]
    run_id = item["runs"][0]["id"]
    online_worker(http, bench_org, [CPU_AUDIO])

    admin_only = [
        ("GET", f"{BASE}/candidates", None),
        ("GET", f"{BASE}/items", None),
        ("GET", f"{BASE}/items/{item_id}", None),
        ("GET", f"{BASE}/items/{item_id}/file", None),
        ("PUT", f"{BASE}/items/{item_id}/reference", {"text": "x"}),
        ("POST", f"{BASE}/items/{item_id}/runs", {"candidates": [dump(CPU_AUDIO)]}),
        ("GET", f"{BASE}/items/{item_id}/runs/{run_id}", None),
        ("DELETE", f"{BASE}/items/{item_id}/runs/{run_id}", None),
        ("GET", f"{BASE}/leaderboard", None),
        ("DELETE", f"{BASE}/items/{item_id}", None),
    ]
    for method, path, body in admin_only:
        resp = http.request(method, path, headers=client, json=body)
        assert resp.status_code == 403, (method, path)
    # the client's rejected calls changed nothing
    assert get_item(http, admin, item_id)["reference"] is None
    assert len(get_item(http, admin, item_id)["runs"]) == 1

    assert http.get(f"{BASE}/status", headers=client).status_code == 200
    assert post_item(http, client, wav_bytes).status_code == 201

    # the hub operator never reaches testbench content, whatever the route
    assert http.get(f"{BASE}/status", headers=operator).status_code == 403
    assert post_item(http, operator, wav_bytes).status_code == 403
    for method, path, body in admin_only:
        resp = http.request(method, path, headers=operator, json=body)
        assert resp.status_code == 403, (method, path)

    anonymous = http.get(f"{BASE}/status")
    assert anonymous.status_code == 401


def test_other_organizations_ids_are_404(
    http, bench_org, store_settings, admin_headers, client_headers, wav_bytes
):
    admin = admin_headers(bench_org)
    item = contribute(http, admin, wav_bytes)
    item_id, run_id = item["id"], item["runs"][0]["id"]
    online_worker(http, bench_org, [CPU_AUDIO])

    from resona_api.admin import create_organization
    import uuid
    other = f"org-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session:
        create_organization(session, other)
    store_settings(other, testbench_enabled=True)
    foreign = admin_headers(other)
    online_worker(http, other, [CPU_AUDIO])

    own = contribute(http, foreign, wav_bytes)
    for method, path, body in [
        ("GET", f"{BASE}/items/{item_id}", None),
        ("GET", f"{BASE}/items/{item_id}/file", None),
        ("PUT", f"{BASE}/items/{item_id}/reference", {"text": "x"}),
        ("POST", f"{BASE}/items/{item_id}/runs", {"candidates": [dump(CPU_AUDIO)]}),
        ("GET", f"{BASE}/items/{item_id}/runs/{run_id}", None),
        ("DELETE", f"{BASE}/items/{item_id}/runs/{run_id}", None),
        ("DELETE", f"{BASE}/items/{item_id}", None),
        # a run of the right organization under an item that is not its own
        ("GET", f"{BASE}/items/{own['id']}/runs/{run_id}", None),
    ]:
        resp = http.request(method, path, headers=foreign, json=body)
        assert resp.status_code == 404, (method, path)

    assert http.get(f"{BASE}/items", headers=foreign).json()["items"][0]["id"] == own["id"]
    assert len(http.get(f"{BASE}/items", headers=foreign).json()["items"]) == 1
    assert http.get(f"{BASE}/status", headers=foreign).json()["items"] == 1
    assert get_item(http, admin, item_id)["status"] == "awaiting_draft"  # untouched


# -- contribute -----------------------------------------------------------

def test_contribute_queues_a_draft_run_that_a_worker_finishes(
    http, bench_org, admin_headers, client_headers, wav_bytes
):
    admin, client = admin_headers(bench_org), client_headers(bench_org)
    # the worker is on release 0.1.0, which the hub never promoted, and reports a tuple
    # that is in no execution profile: both must not matter for a testbench job
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    sid = storage_id(bench_org)

    item = contribute(http, client, wav_bytes, label="  Visite  ", language="de")
    item_id = item["id"]
    assert item["kind"] == "audio" and item["language"] == "de" and item["label"] == "Visite"
    assert item["content_type"] == "audio/wav" and item["size"] == len(wav_bytes)
    assert item["sha256"] == hashlib.sha256(wav_bytes).hexdigest()
    assert item["status"] == "awaiting_draft" and item["draft_status"] == "queued"
    assert item["run_count"] == 1 and item["reference"] is None and item["reference_set_at"] is None
    assert item["created_by"].startswith("cred_")
    draft = item["draft"]
    assert draft["role"] == "draft" and draft["candidate"] == dump(CPU_AUDIO)
    assert draft["status"] == "queued" and draft["queue_reason"] == "waiting_capacity"
    assert draft["text"] is None and draft["wer"] is None and "alignment" not in draft
    assert [r["id"] for r in item["runs"]] == [draft["id"]]
    assert storage.testbench_item_path(sid, item_id).read_bytes() == wav_bytes

    job = job_row(run_job_id(draft["id"]))
    snapshot = json.loads(job.execution_snapshot)
    assert job.purpose == "testbench" and job.private is True and job.status == JobStatus.PENDING
    assert [c for c in snapshot["candidates"]] == [dump(CPU_AUDIO)]
    assert snapshot.get("postprocess") is None
    assert snapshot["policy"]["private"] is True

    claimed = claim(http, worker, [CPU_AUDIO]).json()
    assert claimed["job_id"] == job.id
    assert claimed["execution_snapshot"]["candidate"] == dump(CPU_AUDIO)
    downloaded = http.get(f"/v1/workers/jobs/{job.id}/input", headers={**worker, "X-Resona-Lease": claimed["lease_token"]})
    assert downloaded.content == wav_bytes
    mid = get_item(http, admin, item_id)
    assert mid["draft_status"] == "processing" and mid["status"] == "awaiting_draft"

    posted = post_result(
        http, job.id, worker, claimed["lease_token"],
        result_body("Hallo Welt", seconds=2.5, accelerator="cpu", warnings=["w1"]),
    )
    assert posted.status_code == 200

    done = get_item(http, admin, item_id)
    assert done["status"] == "needs_correction" and done["draft_status"] == "completed"
    draft = done["draft"]
    assert draft["text"] == "Hallo Welt" and draft["warnings"] == ["w1"]
    assert draft["seconds"] == 2.5 and draft["device"] == "cpu"
    assert draft["wer"] is None and draft["completed_at"] is not None
    assert run_path(bench_org, draft["id"]).is_file()
    # the job's own input is consumed, the item's persistent original is not
    assert not storage.job_input_path(sid, job.id).exists()
    assert storage.testbench_item_path(sid, item_id).read_bytes() == wav_bytes
    assert http.get(f"{BASE}/status", headers=client).json() == {
        "enabled": True, "items": 1, "corrected": 0, "runs": 1,
    }


def test_draft_falls_back_to_the_default_profile_without_workers(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    audio = contribute(http, admin, wav_bytes)
    assert audio["draft"]["candidate"] == dump(MAC_AUDIO)
    assert audio["draft"]["queue_reason"] == "no_capable_worker"

    scan = contribute(http, admin, PDF, name="scan.pdf", ctype="application/pdf")
    assert scan["kind"] == "document" and scan["content_type"] == "application/pdf"
    assert scan["draft"]["candidate"] == dump(MAC_DOC)


def test_draft_prefers_the_default_profiles_order_among_available_candidates(
    http, bench_org, admin_headers, wav_bytes
):
    online_worker(http, bench_org, [CPU_AUDIO, MAC_AUDIO])
    item = contribute(http, admin_headers(bench_org), wav_bytes)
    assert item["draft"]["candidate"] == dump(MAC_AUDIO)


def test_contribute_validates_the_upload(http, bench_org, client_headers, wav_bytes, monkeypatch):
    client = client_headers(bench_org)
    assert post_item(http, client, b"").status_code == 422
    assert post_item(http, client, b"this is not media at all").status_code == 415
    for form, code in (({"label": "x" * 121}, "invalid_label"), ({"language": "DE"}, "invalid_language")):
        resp = post_item(http, client, wav_bytes, **form)
        assert resp.status_code == 422 and error_code(resp) == code
    monkeypatch.setattr(jobs_routes, "MAX_UPLOAD_BYTES", 64)
    assert post_item(http, client, wav_bytes).status_code == 413
    monkeypatch.undo()

    assert http.get(f"{BASE}/status", headers=client).json()["items"] == 0
    uploads = storage.org_dir(storage_id(bench_org)) / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())
    assert post_item(http, client, wav_bytes).status_code == 201


def test_item_file_serves_the_original_bytes(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    item = contribute(http, admin, wav_bytes)
    resp = http.get(f"{BASE}/items/{item['id']}/file", headers=admin)
    assert resp.status_code == 200 and resp.content == wav_bytes
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.headers["content-disposition"] == "inline"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"

    item_path(bench_org, item["id"]).unlink()
    gone = http.get(f"{BASE}/items/{item['id']}/file", headers=admin)
    assert gone.status_code == 410 and error_code(gone) == "content_unavailable"


def test_items_list_filters_and_pages(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO, CPU_DOC])
    first = contribute(http, admin, wav_bytes, label="one")
    second = contribute(http, admin, wav_bytes, label="two")
    scan = contribute(http, admin, PDF, name="s.pdf", ctype="application/pdf", label="scan")
    finish_next(http, worker, [CPU_AUDIO, CPU_DOC], "eins")  # first's draft
    put_reference(http, admin, first["id"], "eins")

    page = http.get(f"{BASE}/items?limit=2", headers=admin).json()
    assert [i["id"] for i in page["items"]] == [scan["id"], second["id"]] and page["next_cursor"]
    rest = http.get(f"{BASE}/items?limit=2&cursor={page['next_cursor']}", headers=admin).json()
    assert [i["id"] for i in rest["items"]] == [first["id"]] and rest["next_cursor"] is None

    def ids(query):
        return [i["id"] for i in http.get(f"{BASE}/items?{query}", headers=admin).json()["items"]]

    assert ids("kind=document") == [scan["id"]]
    assert ids("status=corrected") == [first["id"]]
    assert ids("status=awaiting_draft") == [scan["id"], second["id"]]
    assert ids("status=needs_correction") == []
    summary = http.get(f"{BASE}/items?status=corrected", headers=admin).json()["items"][0]
    assert summary["label"] == "one" and "runs" not in summary and summary["run_count"] == 1

    for query in ("status=bogus", "kind=video", "limit=0", "limit=101", "cursor=@@"):
        assert http.get(f"{BASE}/items?{query}", headers=admin).status_code == 422, query


# -- reference and scoring ------------------------------------------------

def test_reference_scores_the_draft_and_rescores_when_it_changes(
    http, bench_org, admin_headers, wav_bytes
):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    finish_next(http, worker, [CPU_AUDIO], "Hallo Welt")

    corrected = put_reference(http, admin, item["id"], "Hallo Welt heute")
    assert corrected["status"] == "corrected" and corrected["reference"] == "Hallo Welt heute"
    assert corrected["reference_set_at"] is not None
    draft = corrected["draft"]
    assert draft["wer"] == pytest.approx(1 / 3) and draft["cer"] > 0
    assert "alignment" not in draft  # the item detail keeps its draft light
    assert corrected["runs"][0]["wer"] == pytest.approx(1 / 3)

    detail = http.get(f"{BASE}/items/{item['id']}/runs/{draft['id']}", headers=admin).json()
    assert detail["alignment"] == [
        {"op": "equal", "ref": "hallo", "hyp": "hallo"},
        {"op": "equal", "ref": "welt", "hyp": "welt"},
        {"op": "delete", "ref": "heute", "hyp": None},
    ]

    again = put_reference(http, admin, item["id"], "hallo, welt!")  # normalization ignores case + punctuation
    assert again["draft"]["wer"] == 0.0 and again["draft"]["cer"] == 0.0
    detail = http.get(f"{BASE}/items/{item['id']}/runs/{draft['id']}", headers=admin).json()
    assert {e["op"] for e in detail["alignment"]} == {"equal"}

    blank = http.put(f"{BASE}/items/{item['id']}/reference", json={"text": "   "}, headers=admin)
    assert blank.status_code == 422 and error_code(blank) == "invalid_reference"
    assert http.put(f"{BASE}/items/{item['id']}/reference", json={}, headers=admin).status_code == 422
    assert get_item(http, admin, item["id"])["reference"] == "hallo, welt!"


def test_alignment_is_absent_before_a_reference_or_a_completed_run(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    run_id = item["draft"]["id"]
    queued = http.get(f"{BASE}/items/{item['id']}/runs/{run_id}", headers=admin).json()
    assert queued["alignment"] is None and queued["text"] is None

    finish_next(http, worker, [CPU_AUDIO], "text ohne referenz")
    no_reference = http.get(f"{BASE}/items/{item['id']}/runs/{run_id}", headers=admin).json()
    assert no_reference["text"] == "text ohne referenz" and no_reference["alignment"] is None


def test_document_items_score_the_markdown_not_the_raw_text(http, bench_org, admin_headers):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_DOC])
    item = contribute(http, admin, PDF, name="scan.pdf", ctype="application/pdf")
    assert item["draft"]["candidate"] == dump(CPU_DOC)
    finish_next(
        http, worker, [CPU_DOC], "ignored raw text", kind="document", seconds=4.0,
        markdown="# Titel\n\nErster Absatz",
    )

    done = get_item(http, admin, item["id"])
    assert done["draft"]["text"] == "# Titel\n\nErster Absatz"
    assert done["draft"]["device"] == "cpu" and done["draft"]["seconds"] == 4.0
    scored = put_reference(http, admin, item["id"], "Titel\n\nErster Absatz")
    assert scored["draft"]["wer"] == 0.0  # markdown-v1: heading marks are punctuation


# -- bench runs -----------------------------------------------------------

def test_bench_runs_metrics_alignment_and_a_leaderboard_that_excludes_drafts(
    http, bench_org, admin_headers, wav_bytes
):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO, MAC_AUDIO])
    caps = [CPU_AUDIO, MAC_AUDIO]
    item = contribute(http, admin, wav_bytes)
    assert item["draft"]["candidate"] == dump(MAC_AUDIO)
    finish_next(http, worker, caps, "Hallo Welt", seconds=0.5, accelerator="metal")  # draft: misses a word
    put_reference(http, admin, item["id"], "Hallo Welt heute")

    listed = http.get(f"{BASE}/candidates", headers=admin).json()["items"]
    assert [(e["kind"], e["candidate"], e["workers_online"]) for e in listed] == [
        ("audio", dump(CPU_AUDIO), 1), ("audio", dump(MAC_AUDIO), 1),
    ]

    created = http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                        json={"candidates": [dump(CPU_AUDIO), dump(MAC_AUDIO)]})
    assert created.status_code == 201
    runs = created.json()["items"]
    assert [(r["role"], r["candidate"], r["status"]) for r in runs] == [
        ("bench", dump(CPU_AUDIO), "queued"), ("bench", dump(MAC_AUDIO), "queued"),
    ]
    assert all(r["queue_reason"] == "waiting_capacity" for r in runs)
    # asking again while both are active skips them
    again = http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                      json={"candidates": [dump(CPU_AUDIO)]})
    assert again.status_code == 201 and again.json() == {"items": []}

    answers = {"faster-whisper": ("hallo welt heut", 3.0, "cpu"), "mlx-whisper": ("Hallo Welt heute", 0.75, "metal")}
    for _ in range(2):
        granted = claim(http, worker, caps).json()
        engine_name = granted["execution_snapshot"]["candidate"]["engine"]
        text, seconds, device = answers[engine_name]
        posted = post_result(http, granted["job_id"], worker, granted["lease_token"],
                             result_body(text, seconds=seconds, accelerator=device))
        assert posted.status_code == 200
    assert claim(http, worker, caps).status_code == 204

    detail = get_item(http, admin, item["id"])
    assert detail["run_count"] == 3 and detail["status"] == "corrected"
    by_engine = {r["candidate"]["engine"]: r for r in detail["runs"] if r["role"] == "bench"}
    cpu, mac = by_engine["faster-whisper"], by_engine["mlx-whisper"]
    assert cpu["status"] == "completed" and cpu["wer"] == pytest.approx(1 / 3)
    assert cpu["seconds"] == 3.0 and cpu["device"] == "cpu"
    assert mac["wer"] == 0.0 and mac["seconds"] == 0.75 and mac["device"] == "metal"

    cpu_detail = http.get(f"{BASE}/items/{item['id']}/runs/{cpu['id']}", headers=admin).json()
    assert cpu_detail["text"] == "hallo welt heut"
    assert cpu_detail["alignment"][-1] == {"op": "substitute", "ref": "heute", "hyp": "heut"}
    assert [e["op"] for e in cpu_detail["alignment"][:2]] == ["equal", "equal"]
    mac_detail = http.get(f"{BASE}/items/{item['id']}/runs/{mac['id']}", headers=admin).json()
    assert {e["op"] for e in mac_detail["alignment"]} == {"equal"}

    board = http.get(f"{BASE}/leaderboard", headers=admin).json()
    assert board["corrected_items"] == 1
    # the MAC candidate also produced the (imperfect) draft; the draft must not count
    assert [(r["candidate"]["engine"], r["items"], r["wer_mean"], r["seconds_mean"]) for r in board["items"]] == [
        ("mlx-whisper", 1, 0.0, 0.75), ("faster-whisper", 1, pytest.approx(1 / 3), 3.0),
    ]
    assert board["items"][0]["kind"] == "audio" and board["items"][0]["candidate"] == dump(MAC_AUDIO)
    assert board["items"][0]["cer_mean"] == 0.0

    # a changed reference re-scores bench runs too, and the ranking follows
    put_reference(http, admin, item["id"], "hallo welt heut")
    board = http.get(f"{BASE}/leaderboard", headers=admin).json()
    assert [r["candidate"]["engine"] for r in board["items"]] == ["faster-whisper", "mlx-whisper"]
    assert board["items"][0]["wer_mean"] == 0.0 and board["items"][1]["wer_mean"] == pytest.approx(1 / 3)


def test_runs_are_limited_to_candidates_a_worker_has_tested(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    item = contribute(http, admin, wav_bytes)
    online_worker(
        http, bench_org, [CPU_AUDIO, CPU_DOC, CLOUD],
        approved=[CPU_AUDIO, CPU_DOC, CLOUD, CUDA_AUDIO],  # CUDA approved but never tested
    )
    # a non-private worker and a revoked worker do not widen the set
    online_worker(http, bench_org, [MAC_AUDIO], private=False)
    revoked_id, _ = online_worker(http, bench_org, [MAC_DOC])
    with Session(engine) as session:
        revoke_worker(session, revoked_id)
        session.commit()

    listed = http.get(f"{BASE}/candidates", headers=admin).json()["items"]
    assert [(e["kind"], e["candidate"]["engine"]) for e in listed] == [
        ("audio", "faster-whisper"), ("document", "docling"),
    ]

    def runs(*candidates):
        return http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                         json={"candidates": [dump(c) for c in candidates]})

    for candidate in (CUDA_AUDIO, MAC_AUDIO, CLOUD, CPU_DOC):  # untested, non-private, cloud, wrong kind
        rejected = runs(CPU_AUDIO, candidate)
        assert rejected.status_code == 422, candidate
        assert error_code(rejected) == "unavailable_candidate"
    assert get_item(http, admin, item["id"])["run_count"] == 1  # nothing was queued by the rejected calls
    assert runs().status_code == 422
    assert runs(*[CPU_AUDIO] * 17).status_code == 422

    queued = runs(CPU_AUDIO, CPU_AUDIO)  # duplicates in one request collapse
    assert queued.status_code == 201 and len(queued.json()["items"]) == 1


def test_workers_offline_for_a_while_are_listed_with_zero_online(http, bench_org, admin_headers, at):
    admin = admin_headers(bench_org)
    online_worker(http, bench_org, [CPU_AUDIO])
    listed = http.get(f"{BASE}/candidates", headers=admin).json()["items"]
    assert listed[0]["workers_online"] == 1
    at(T0 + timedelta(minutes=5))
    listed = http.get(f"{BASE}/candidates", headers=admin).json()["items"]
    assert [(e["candidate"]["engine"], e["workers_online"]) for e in listed] == [("faster-whisper", 0)]


def test_delete_run_removes_its_result_and_cancels_an_active_job(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    finish_next(http, worker, [CPU_AUDIO], "text")
    put_reference(http, admin, item["id"], "text")
    queued = http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                       json={"candidates": [dump(CPU_AUDIO)]}).json()["items"][0]
    queued_job = run_job_id(queued["id"])
    draft_id = item["draft"]["id"]
    assert run_path(bench_org, draft_id).is_file()

    assert http.delete(f"{BASE}/items/{item['id']}/runs/{queued['id']}", headers=admin).status_code == 204
    assert job_row(queued_job) is None
    assert claim(http, worker, [CPU_AUDIO]).status_code == 204  # nothing left to claim

    assert http.delete(f"{BASE}/items/{item['id']}/runs/{draft_id}", headers=admin).status_code == 204
    assert not run_path(bench_org, draft_id).exists()
    remaining = get_item(http, admin, item["id"])
    assert remaining["run_count"] == 0 and remaining["draft"] is None and remaining["status"] == "corrected"
    gone = http.get(f"{BASE}/items/{item['id']}/runs/{draft_id}", headers=admin)
    assert gone.status_code == 404 and error_code(gone) == "run_not_found"


# -- delete ---------------------------------------------------------------

def test_delete_item_removes_rows_files_and_cancels_pending_jobs(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    sid = storage_id(bench_org)
    item = contribute(http, admin, wav_bytes)  # no worker: the draft job stays pending
    job_id = run_job_id(item["draft"]["id"])
    assert storage.job_input_path(sid, job_id).is_file() and item_path(bench_org, item["id"]).is_file()

    assert http.delete(f"{BASE}/items/{item['id']}", headers=admin).status_code == 204

    assert not item_path(bench_org, item["id"]).exists()
    assert not storage.job_dir(sid, job_id).exists() and job_row(job_id) is None
    with Session(engine) as session:
        assert session.exec(select(TestbenchItem)).all() == []
        assert session.exec(select(TestbenchRun)).all() == []
    assert http.get(f"{BASE}/items/{item['id']}", headers=admin).status_code == 404
    assert http.delete(f"{BASE}/items/{item['id']}", headers=admin).status_code == 404
    assert http.get(f"{BASE}/status", headers=admin).json() == {"enabled": True, "items": 0, "corrected": 0, "runs": 0}


def test_delete_item_removes_run_results_and_fences_a_running_job(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    finish_next(http, worker, [CPU_AUDIO], "fertig")
    done_run = item["draft"]["id"]
    get_item(http, admin, item["id"])  # collect
    bench = http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                      json={"candidates": [dump(CPU_AUDIO)]}).json()["items"][0]
    claimed = claim(http, worker, [CPU_AUDIO]).json()  # the bench job is now running
    assert run_job_id(bench["id"]) == claimed["job_id"]
    assert run_path(bench_org, done_run).is_file()

    assert http.delete(f"{BASE}/items/{item['id']}", headers=admin).status_code == 204

    assert not run_path(bench_org, done_run).exists()
    assert not item_path(bench_org, item["id"]).exists()
    tombstone = job_row(claimed["job_id"])  # a leased job stays as an invisible tombstone
    assert tombstone.status == JobStatus.CANCELLED and tombstone.error_code == broker.JOB_DELETED
    late = post_result(http, claimed["job_id"], worker, claimed["lease_token"], result_body("zu spaet"))
    assert late.status_code >= 400
    with Session(engine) as session:
        assert session.exec(select(TestbenchRun)).all() == []


# -- failures -------------------------------------------------------------

def test_failed_and_cancelled_jobs_settle_their_runs(
    http, bench_org, admin_headers, client_headers, wav_bytes
):
    admin, client = admin_headers(bench_org), client_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    claimed = claim(http, worker, [CPU_AUDIO]).json()
    failed = http.post(
        f"/v1/workers/jobs/{claimed['job_id']}/fail", headers={**worker, "X-Resona-Lease": claimed["lease_token"]},
        json={"code": "unsupported_input", "message": "cannot decode", "retryable": False},
    )
    assert failed.status_code == 200

    detail = get_item(http, admin, item["id"])
    assert detail["status"] == "awaiting_draft" and detail["draft_status"] == "failed"
    assert detail["draft"]["error_code"] == "unsupported_input" and detail["draft"]["text"] is None

    bench = http.post(f"{BASE}/items/{item['id']}/runs", headers=admin,
                      json={"candidates": [dump(CPU_AUDIO)]}).json()["items"][0]
    cancel = http.post(f"/v1/jobs/{run_job_id(bench['id'])}/cancel", headers=client)
    assert cancel.status_code == 200
    run = http.get(f"{BASE}/items/{item['id']}/runs/{bench['id']}", headers=admin).json()
    assert run["status"] == "cancelled" and run["completed_at"] is not None and run["alignment"] is None
    # a failed draft does not block scoring or new runs: the admin can still write a reference
    assert put_reference(http, admin, item["id"], "von hand")["status"] == "corrected"
    leaderboard = http.get(f"{BASE}/leaderboard", headers=admin).json()
    assert leaderboard == {"corrected_items": 1, "items": []}


def test_a_result_lost_before_capture_fails_the_run(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    claimed = finish_next(http, worker, [CPU_AUDIO], "weg")
    storage.result_path(storage_id(bench_org), claimed["job_id"]).unlink()

    detail = get_item(http, admin, item["id"])
    assert detail["draft_status"] == "failed" and detail["draft"]["error_code"] == "result_unavailable"
    assert detail["status"] == "awaiting_draft"


def test_a_purged_job_fails_its_run_as_lost(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    item = contribute(http, admin, wav_bytes)
    job_id = run_job_id(item["draft"]["id"])
    with Session(engine) as session:
        session.delete(session.get(Job, job_id))
        session.commit()
    detail = get_item(http, admin, item["id"])
    assert detail["draft_status"] == "failed" and detail["draft"]["error_code"] == "job_lost"


# -- retention ------------------------------------------------------------

def test_collector_survives_result_ttl_expiry_and_job_purge(
    http, bench_org, admin_headers, client_headers, wav_bytes, at
):
    admin, client = admin_headers(bench_org), client_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    sid = storage_id(bench_org)
    item = contribute(http, admin, wav_bytes)
    claimed = finish_next(http, worker, [CPU_AUDIO], "Hallo Welt", seconds=1.5)
    job_id = claimed["job_id"]
    # nobody reads the testbench; the 24 h result retention is about to pass
    assert storage.result_path(sid, job_id).is_file()

    at(T0 + timedelta(hours=25))
    counts = maintenance.run_once()
    assert counts["testbench_runs_settled"] == 1
    assert counts["results_expired"] >= 1
    assert not storage.result_path(sid, job_id).exists()
    assert http.get(f"/v1/jobs/{job_id}/result", headers=client).status_code == 410

    detail = get_item(http, admin, item["id"])
    assert detail["draft_status"] == "completed" and detail["status"] == "needs_correction"
    assert detail["draft"]["text"] == "Hallo Welt" and detail["draft"]["seconds"] == 1.5
    assert detail["draft"]["completed_at"] is not None
    assert run_path(bench_org, item["draft"]["id"]).is_file()

    at(T0 + timedelta(days=8))  # job rows go after seven days; the run keeps its own data
    counts = maintenance.run_once()
    assert counts["metadata_deleted"] >= 1
    assert job_row(job_id) is None
    assert item_path(bench_org, item["id"]).read_bytes() == wav_bytes
    scored = put_reference(http, admin, item["id"], "Hallo Welt heute")
    assert scored["draft"]["status"] == "completed" and scored["draft"]["wer"] == pytest.approx(1 / 3)
    assert maintenance.run_once()["testbench_runs_settled"] == 0


def test_lazy_reads_capture_without_a_maintenance_pass(http, bench_org, admin_headers, wav_bytes):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    item = contribute(http, admin, wav_bytes)
    finish_next(http, worker, [CPU_AUDIO], "lazy")
    with Session(engine) as session:
        assert session.get(TestbenchRun, item["draft"]["id"]).status == "queued"  # not yet settled

    listed = http.get(f"{BASE}/items", headers=admin).json()["items"][0]
    assert listed["draft_status"] == "completed" and listed["status"] == "needs_correction"


def test_maintenance_never_reaps_testbench_files_but_sweeps_their_staging(
    http, bench_org, admin_headers, wav_bytes, at
):
    admin = admin_headers(bench_org)
    _, worker = online_worker(http, bench_org, [CPU_AUDIO])
    sid = storage_id(bench_org)
    item = contribute(http, admin, wav_bytes)
    finish_next(http, worker, [CPU_AUDIO], "bleibt")
    get_item(http, admin, item["id"])
    leftover = storage.testbench_dir(sid) / f"{storage.STAGING_PREFIX}abandoned"
    leftover.write_bytes(b"partial")

    for days in (2, 10, 60):
        at(T0 + timedelta(days=days))
        maintenance.run_once()

    assert not leftover.exists()
    assert item_path(bench_org, item["id"]).read_bytes() == wav_bytes
    assert run_path(bench_org, item["draft"]["id"]).is_file()
    detail = get_item(http, admin, item["id"])
    assert detail["draft"]["text"] == "bleibt" and detail["draft"]["status"] == "completed"
