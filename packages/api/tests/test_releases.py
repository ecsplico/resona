"""Release manifests, staging, the hub version, evaluation-purpose jobs and release-gated claims."""

import io
import json
import struct
import uuid
import wave

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_job_core import Candidate
from resona_job_core.release import ReleaseManifest
from resona_api import releases
from resona_api.admin import approve_worker, enroll_worker, main
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus, Release, Worker


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    return buf.getvalue()


WAV = _wav()

MAC_AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
DIGEST = "sha256:" + "a" * 64
COMMIT = "49e6aa286ad60c14352c404340ded53710378a11"


def manifest_body(release_id: str, **changes) -> dict:
    body = {
        "release_id": release_id,
        "created_at": "2026-11-01T10:00:00Z",
        "wheels": [{"name": "resona-worker", "version": release_id, "sha256": "b" * 64}],
        "images": [{"name": "ghcr.io/ecsplico/resona-hub", "digest": DIGEST}],
        "models": [{"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx", "revision": COMMIT}],
        "worker_protocol": 1,
    }
    return {**body, **changes}


def new_release_id() -> str:
    return f"9.0.0+t{uuid.uuid4().hex[:10]}"


def stage(release_id: str) -> None:
    with Session(engine) as session:
        releases.stage_release(session, json.dumps(manifest_body(release_id)).encode())


@pytest.fixture
def http(test_app):
    with TestClient(test_app) as c:
        yield c


def error_code(response) -> str:
    body = response.json()
    return body.get("error") or body["detail"]["error"]


# -- manifest validation --------------------------------------------------------

@pytest.mark.parametrize(
    "changes",
    [
        {"images": [{"name": "ghcr.io/ecsplico/resona-hub:latest", "digest": DIGEST}]},
        {"images": [{"name": "ghcr.io/ecsplico/resona-worker:cuda", "digest": DIGEST}]},
        {"images": [{"name": "ghcr.io/ecsplico/resona-hub", "digest": "latest"}]},
        {"images": [{"name": "ghcr.io/ecsplico/resona-hub"}]},
        {"models": [{"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx", "revision": "main"}]},
        {"models": [{"engine": "whisper", "model": "openai/whisper-large-v3", "revision": "v1.0"}]},
        {"wheels": [{"name": "resona-worker", "version": "0.2.0", "sha256": "not-a-hash"}]},
        {"worker_protocol": 2},
        {"extra": "field"},
    ],
)
def test_mutable_or_unpinned_manifest_is_rejected(changes) -> None:
    with pytest.raises(releases.ReleaseError):
        releases.parse_manifest(json.dumps(manifest_body("0.2.0", **changes)).encode())


def test_committed_json_schema_matches_the_manifest_model() -> None:
    from pathlib import Path

    schema_file = Path(__file__).resolve().parents[3] / "release" / "manifest.schema.json"
    assert json.loads(schema_file.read_text(encoding="utf-8")) == ReleaseManifest.model_json_schema()


def test_admin_release_stage_stores_staged_and_refuses_a_conflicting_manifest(tmp_path, capsys) -> None:
    release_id = new_release_id()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_body(release_id)), encoding="utf-8")

    assert main(["release", "stage", "--manifest", str(path)]) == 0
    assert main(["release", "stage", "--manifest", str(path)]) == 0  # identical bytes: no-op
    with Session(engine) as session:
        row = session.get(Release, release_id)
        assert row.status == "staged"
        assert row.manifest_sha256 == ReleaseManifest.model_validate(manifest_body(release_id)).digest()

    path.write_text(json.dumps(manifest_body(release_id, models=[])), encoding="utf-8")
    assert main(["release", "stage", "--manifest", str(path)]) == 1
    assert "different manifest" in capsys.readouterr().err

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(manifest_body(new_release_id(), images=[{"name": "ghcr.io/x/y:latest", "digest": DIGEST}])))
    assert main(["release", "stage", "--manifest", str(bad)]) == 1

    capsys.readouterr()
    assert main(["release", "list"]) == 0
    assert release_id in capsys.readouterr().out


def test_staging_the_bundled_release_fills_in_its_manifest_and_keeps_it_bundled(monkeypatch) -> None:
    release_id = new_release_id()
    monkeypatch.setenv("RESONA_RELEASE_ID", release_id)
    body = json.dumps(manifest_body(release_id)).encode()
    with Session(engine) as session:
        releases.record_bundled_release(session)
        placeholder = session.get(Release, release_id).manifest_sha256

        staged = releases.stage_release(session, body)

        assert staged.status == "bundled"
        assert staged.manifest_sha256 == ReleaseManifest.model_validate_json(body).digest() != placeholder
        with pytest.raises(releases.ReleaseError):
            releases.stage_release(session, json.dumps(manifest_body(release_id, models=[])).encode())



# -- version ----------------------------------------------------------------------

def test_version_needs_no_credential(anon_client) -> None:
    response = anon_client.get("/v1/version")

    assert response.status_code == 200
    assert response.json() == {
        "release_id": releases.hub_release_id(),
        "hub_version": releases.hub_version(),
        "worker_protocol": 1,
    }


# -- evaluation-purpose jobs ------------------------------------------------------

def _upload(http, headers) -> str:
    response = http.post("/v1/uploads", files={"file": ("a.wav", WAV, "audio/wav")}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["upload_id"]


def _submit(http, headers, **fields):
    body = {"kind": "audio", "upload_id": _upload(http, headers), **fields}
    return http.post("/v1/jobs", json=body, headers={**headers, "Idempotency-Key": uuid.uuid4().hex})


def test_evaluation_jobs_need_org_admin_and_a_staged_release(http, fresh_org, client_headers, admin_headers) -> None:
    release_id = new_release_id()
    stage(release_id)
    admin = admin_headers(fresh_org)

    client = _submit(http, client_headers(fresh_org), purpose="evaluation", release_id=release_id)
    assert client.status_code == 403
    missing = _submit(http, admin, purpose="evaluation")
    assert (missing.status_code, error_code(missing)) == (422, "release_not_staged")
    unknown = _submit(http, admin, purpose="evaluation", release_id=new_release_id())
    assert (unknown.status_code, error_code(unknown)) == (422, "release_not_staged")
    production_with_release = _submit(http, admin, release_id=release_id)
    assert production_with_release.status_code == 422

    accepted = _submit(http, admin, purpose="evaluation", release_id=release_id)
    assert accepted.status_code == 202, accepted.text


# -- release-gated claims ---------------------------------------------------------

def _worker(org_id: str, runtime_version: str) -> dict[str, str]:
    with Session(engine) as session:
        worker_id, token = enroll_worker(session, "release-worker", org_id)
        approve_worker(session, worker_id, private=True, capabilities=[MAC_AUDIO.model_dump(mode="json")])
        worker = session.get(Worker, worker_id)
        worker.runtime_version = runtime_version
        session.add(worker)
        session.commit()
    return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}


def _claim(http, headers):
    return http.post("/v1/workers/claim?wait=0",
                     json={"capabilities": [MAC_AUDIO.model_dump(mode="json")]}, headers=headers)


def _evaluation(http, admin, release_id: str, **changes) -> int:
    body = {
        "profile": "mac-private", "execution_profile": "private-auto", "platform": "mac-metal",
        "release_id": release_id, "corpus_sha256": "c" * 64, "baseline_revision": None,
        "fixtures": {"de-short": {"wer": 0.05, "cer": 0.02, "silence_insertions": 0}},
        "gate": {"passed": True, "failures": []},
        **changes,
    }
    response = http.post("/v1/evaluations", json=body, headers=admin)
    assert response.status_code == 201, response.text
    return response.json()["evaluation_id"]


def test_unapproved_release_claims_only_its_evaluation_jobs_until_promoted(
    http, fresh_org, client_headers, admin_headers
) -> None:
    release_id = new_release_id()
    stage(release_id)
    admin, client = admin_headers(fresh_org), client_headers(fresh_org)
    candidate = _worker(fresh_org, release_id)
    bundled = _worker(fresh_org, releases.hub_release_id())

    production = _submit(http, client).json()["job_id"]
    assert _claim(http, candidate).status_code == 204

    evaluation = _submit(http, admin, purpose="evaluation", release_id=release_id).json()["job_id"]
    claimed = _claim(http, candidate)
    assert claimed.status_code == 200
    assert claimed.json()["job_id"] == evaluation
    assert claimed.json()["execution_snapshot"]["release_id"] == release_id
    assert _claim(http, candidate).status_code == 204  # still no production work

    # The hub's bundled release runs production work but never another release's evaluation.
    other = _submit(http, admin, purpose="evaluation", release_id=release_id).json()["job_id"]
    assert _claim(http, bundled).json()["job_id"] == production
    assert _claim(http, bundled).status_code == 204
    with Session(engine) as session:
        assert session.get(Job, other).status == JobStatus.PENDING

    evaluation_id = _evaluation(http, admin, release_id)
    promoted = http.post(f"/v1/evaluations/{evaluation_id}/promote",
                         json={"expected_baseline_revision": None}, headers=admin)
    assert promoted.status_code == 200, promoted.text

    later = _submit(http, client).json()["job_id"]
    picked = {_claim(http, candidate).json()["job_id"], _claim(http, candidate).json()["job_id"]}
    assert picked == {other, later}


def test_approval_is_per_organization_and_platform(http, fresh_org, admin_headers, client_headers) -> None:
    from resona_api.admin import create_organization

    release_id = new_release_id()
    stage(release_id)
    other_org = f"org-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session:
        create_organization(session, other_org)
    evaluation_id = _evaluation(http, admin_headers(fresh_org), release_id, platform="cpu")
    assert http.post(f"/v1/evaluations/{evaluation_id}/promote", json={"expected_baseline_revision": None},
                     headers=admin_headers(fresh_org)).status_code == 200

    _submit(http, client_headers(fresh_org))
    _submit(http, client_headers(other_org))
    # Approved for cpu in fresh_org only: a metal worker on that release claims nothing in either org.
    assert _claim(http, _worker(fresh_org, release_id)).status_code == 204
    assert _claim(http, _worker(other_org, release_id)).status_code == 204


# -- worker protocol ----------------------------------------------------------------

def test_incompatible_protocol_gets_426_before_any_input_download(http, fresh_org, client_headers) -> None:
    bundled = _worker(fresh_org, releases.hub_release_id())
    job_id = _submit(http, client_headers(fresh_org)).json()["job_id"]
    claimed = _claim(http, bundled).json()
    lease = {"X-Resona-Lease": claimed["lease_token"]}

    for protocol in (None, "2", "x"):
        headers = {"Authorization": bundled["Authorization"], **lease}
        if protocol is not None:
            headers["X-Resona-Worker-Protocol"] = protocol
        for method, path in (
            ("get", f"/v1/workers/jobs/{job_id}/input"),
            ("get", f"/v1/workers/jobs/{job_id}/postprocess"),
            ("post", f"/v1/workers/claim?wait=0"),
            ("get", f"/v1/workers/releases/{releases.hub_release_id()}"),
        ):
            response = getattr(http, method)(path, headers=headers)
            assert response.status_code == 426, (protocol, path)
            assert error_code(response) == "protocol_incompatible"
            assert WAV not in response.content

    downloaded = http.get(f"/v1/workers/jobs/{job_id}/input", headers={**bundled, **lease})
    assert downloaded.status_code == 200
    assert downloaded.content == WAV


def test_worker_release_lookup_reports_staging_and_approval(http, fresh_org, admin_headers) -> None:
    release_id = new_release_id()
    stage(release_id)
    worker = _worker(fresh_org, release_id)

    assert http.get(f"/v1/workers/releases/{new_release_id()}", headers=worker).status_code == 404
    staged = http.get(f"/v1/workers/releases/{release_id}", headers=worker).json()
    assert staged["status"] == "staged" and staged["approved_tuples"] == [] and staged["bundled"] is False

    evaluation_id = _evaluation(http, admin_headers(fresh_org), release_id)
    http.post(f"/v1/evaluations/{evaluation_id}/promote", json={"expected_baseline_revision": None},
              headers=admin_headers(fresh_org))
    approved = http.get(f"/v1/workers/releases/{release_id}", headers=worker).json()
    assert approved["approved_tuples"] == [{"execution_profile": "private-auto", "platform": "mac-metal"}]
