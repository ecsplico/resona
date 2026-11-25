"""``GET /v1/engines?languages=``: recommendation roles from the org's baselines and testbench."""

import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Candidate, JobResult, Provenance, Segment, StageProvenance
from resona_api import clock, releases, storage
from resona_api import engine_registry as reg
from resona_api.admin import approve_worker, enroll_worker
from resona_api.db.engine import engine
from resona_api.db.models import TestbenchItem, TestbenchRun, Worker
from resona_api.testbench import storage_id_of

MAC = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
CUDA = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
CPU = Candidate(engine="faster-whisper", model="Systran/faster-whisper-large-v3", accelerator="cpu")
CORPUS = "d" * 64


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def release_id() -> str:
    rid = f"9.2.0+r{uuid.uuid4().hex[:10]}"
    manifest = {
        "release_id": rid, "created_at": "2026-11-01T10:00:00Z",
        "wheels": [{"name": "resona-worker", "version": rid, "sha256": "b" * 64}],
        "images": [], "models": [], "worker_protocol": 1,
    }
    with Session(engine) as session:
        releases.stage_release(session, json.dumps(manifest).encode())
    return rid


def _worker(org_id: str, caps: list[Candidate]) -> None:
    with Session(engine) as session:
        worker_id, _ = enroll_worker(session, "rec-worker", org_id)
        approve_worker(session, worker_id, private=True, capabilities=[c.model_dump(mode="json") for c in caps])
        worker = session.get(Worker, worker_id)
        worker.reported_capabilities = json.dumps([c.model_dump(mode="json") for c in caps])
        worker.last_seen = clock.now()
        session.add(worker)
        session.commit()


def _baseline(http, admin, release_id, profile, platform, fixtures: dict[str, tuple[str, float, float | None]]):
    """Record and promote an evaluation: fixture id → (language, wer, seconds_per_30s)."""
    metrics, checks = {}, {}
    for fid, (language, wer, speed) in fixtures.items():
        metrics[fid] = {"wer": wer, **({"seconds_per_30s": speed} if speed is not None else {})}
        checks[fid] = {"kind": "audio", "checks": ["wer"], "language": language}
    payload = {
        "profile": profile, "execution_profile": profile, "platform": platform, "release_id": release_id,
        "corpus_sha256": CORPUS, "baseline_revision": None, "fixtures": metrics, "fixture_checks": checks,
        "gate": {"passed": True, "failures": []},
    }
    created = http.post("/v1/evaluations", json=payload, headers=admin)
    assert created.status_code == 201, created.text
    promoted = http.post(f"/v1/evaluations/{created.json()['evaluation_id']}/promote",
                         json={"expected_baseline_revision": None}, headers=admin)
    assert promoted.status_code == 200, promoted.text


def _bench_run(org_id: str, candidate: Candidate, *, language: str, wer: float, asr_seconds: float,
               audio_end: float, role: str = "bench") -> None:
    result = JobResult(
        kind="audio", raw_text="x", markdown="x",
        segments=[Segment(start=0.0, end=audio_end / 2, text="x"), Segment(start=audio_end / 2, end=audio_end, text="y")],
        provenance=Provenance(stages=[StageProvenance(
            stage="asr", engine=candidate.engine, model=candidate.model, accelerator=candidate.accelerator,
            seconds=asr_seconds,
        )]),
    )
    with Session(engine) as session:
        item = TestbenchItem(organization_id=org_id, kind="audio", language=language, content_type="audio/wav",
                             size=1, sha256="0" * 64, reference_text="x", created_by_credential_id="c")
        session.add(item)
        session.commit()
        run = TestbenchRun(item_id=item.id, organization_id=org_id, role=role,
                           candidate=json.dumps(candidate.model_dump(mode="json"), sort_keys=True),
                           status="completed", wer=wer, seconds=asr_seconds)
        session.add(run)
        session.commit()
        storage.write_bytes_atomic(storage.testbench_run_path(storage_id_of(session, org_id), run.id),
                                   result.model_dump_json().encode())


def _recommendations(http, headers, query: str = "") -> dict[str, dict | None]:
    with patch.object(reg, "speech_catalogue", return_value=[]):
        response = http.get(f"/v1/engines{query}", headers=headers)
    assert response.status_code == 200, response.text
    return {
        c["candidate"]["engine"]: c.get("recommendation")
        for e in response.json()["engines"] for c in e["candidates"]
    }


def test_baselines_give_t3_roles_per_language(http, fresh_org, admin_headers, client_headers, release_id):
    _worker(fresh_org, [MAC, CUDA, CPU])
    admin, client = admin_headers(fresh_org), client_headers(fresh_org)
    # T3's fixture for ["en"]: balanced (4 %, 2 s), fast (7 %, 1 s), accurate (2 %, 8 s).
    _baseline(http, admin, release_id, "mac-private", "mac-metal", {"a": ("de", 0.04, 2.0)})
    _baseline(http, admin, release_id, "cpu-private", "cpu", {"a": ("de", 0.07, 1.0)})
    _baseline(http, admin, release_id, "linux-cuda", "linux-cuda", {"a": ("de", 0.01, 8.0), "b": ("de", 0.03, 8.0)})

    roles = _recommendations(http, client)  # default: the organization language (de)

    assert roles["mlx-whisper"] == {"role": "recommended", "error_percent": 4.0, "seconds_per_30s": 2.0,
                                    "basis": "baseline"}
    assert roles["faster-whisper"]["role"] == "fast"
    assert roles["whisper"] == {"role": "accurate", "error_percent": 2.0, "seconds_per_30s": 8.0,
                                "basis": "baseline"}
    assert _recommendations(http, client, "?languages=de") == roles
    # Nothing measured in English: no candidate gets a role, the key stays absent.
    assert set(_recommendations(http, client, "?languages=de,en").values()) == {None}
    with patch.object(reg, "speech_catalogue", return_value=[]):
        body = http.get("/v1/engines?languages=en", headers=client).json()
    assert all("recommendation" not in c for e in body["engines"] for c in e["candidates"])


def test_testbench_fills_in_where_no_baseline_measured_speed(http, fresh_org, admin_headers, client_headers,
                                                              release_id):
    _worker(fresh_org, [MAC, CPU])
    admin, client = admin_headers(fresh_org), client_headers(fresh_org)
    # An older baseline without speed cannot rank its candidate.
    _baseline(http, admin, release_id, "mac-private", "mac-metal", {"a": ("de", 0.02, None)})
    # 1.5 s of ASR for 15 s of audio = 3 s per 30 s.
    _bench_run(fresh_org, CPU, language="de", wer=0.05, asr_seconds=1.5, audio_end=15.0)
    _bench_run(fresh_org, CPU, language="de", wer=0.07, asr_seconds=1.5, audio_end=15.0)
    # Draft runs never count (the reference started as their output).
    _bench_run(fresh_org, MAC, language="de", wer=0.0, asr_seconds=0.1, audio_end=15.0, role="draft")

    roles = _recommendations(http, client, "?languages=de")

    assert roles["faster-whisper"] == {"role": "recommended", "error_percent": 6.0, "seconds_per_30s": 3.0,
                                       "basis": "testbench"}
    assert roles["mlx-whisper"] is None


def test_other_organizations_measurements_never_count(http, fresh_org, org_b, client_headers):
    _worker(fresh_org, [CPU])
    _bench_run(org_b, CPU, language="de", wer=0.05, asr_seconds=1.0, audio_end=10.0)

    assert _recommendations(http, client_headers(fresh_org), "?languages=de") == {"faster-whisper": None}


@pytest.mark.parametrize("languages", ["de,DEUTSCH", "d", "de;en", ",".join(["de"] * 11)])
def test_invalid_languages_are_rejected(http, fresh_org, client_headers, languages):
    response = http.get(f"/v1/engines?languages={languages}", headers=client_headers(fresh_org))

    assert response.status_code == 422
    body = response.json()
    assert (body.get("error") or body["detail"]["error"]) == "invalid_request"
