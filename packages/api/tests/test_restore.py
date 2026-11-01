"""``resona-admin restore`` against an isolated hub database and data directory.

The hub under restore has its own SQLite file, payload directory and lock file;
the module-level engine/paths are pointed at them for the duration of a test.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, create_engine, select

from resona_job_core import Candidate
from resona_api import auth, clock, deps, hublock, storage
from resona_api.admin import approve_worker, create_organization, enroll_worker, issue_credential, main, revoke_credential
from resona_api.auth import resolve_token
from resona_api.db import engine as db_engine
from resona_api.db.models import Credential, Job, JobStatus, Organization, Upload, Worker
from resona_api.hublock import HubRunning
from resona_api.restore import RestoreError, restore

CAND = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx",
                 model_revision="rev-1", accelerator="metal")
ORG = "restore-org"


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """An isolated hub: own database, payload root and lock; migrated to the current schema."""
    db_dir = tmp_path / "db"
    data_dir = tmp_path / "data"
    db_dir.mkdir()
    data_dir.mkdir()
    isolated = create_engine(f"sqlite:///{db_dir / 'hub.sqlite'}", connect_args={"timeout": 5})
    event.listen(isolated, "connect", db_engine._sqlite_pragmas)
    monkeypatch.setattr(db_engine, "engine", isolated)
    monkeypatch.setattr(storage, "DATA_PATH", data_dir)
    monkeypatch.setattr(hublock, "DB_PATH", db_dir)
    db_engine.create_db_and_tables()
    yield tmp_path
    db_engine.engine.dispose()


def _session() -> Session:
    return Session(db_engine.engine)


def _storage_id() -> str:
    with _session() as session:
        return session.get(Organization, ORG).storage_id


def _job(status: JobStatus, *, input_file: bool = False, result_file: bool = False, **fields) -> int:
    now = clock.now()
    with _session() as session:
        job = Job(
            organization_id=ORG,
            kind="audio",
            status=status,
            execution_snapshot=json.dumps({"candidates": [CAND.model_dump(mode="json")]}),
            input_sha256=hashlib.sha256(b"input").hexdigest(),
            deadline_at=now + timedelta(hours=24),
            **fields,
        )
        session.add(job)
        session.commit()
        job_id = job.id
    if input_file:
        storage.write_bytes_atomic(storage.job_input_path(_storage_id(), job_id), b"input")
    if result_file:
        body = json.dumps({"schema_version": 1, "kind": "audio", "raw_text": "Hallo",
                           "markdown": "Hallo", "provenance": {"stages": []}}).encode()
        storage.write_bytes_atomic(storage.result_path(_storage_id(), job_id), body)
    return job_id


def _backup(hub_dir: Path) -> Path:
    target = hub_dir / "backup.sqlite"
    src = sqlite3.connect(db_engine.engine.url.database)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def _api(token: str) -> TestClient:
    from resona_api.app import app

    def session():
        with _session() as s:
            yield s

    app.dependency_overrides[deps.get_db_session] = session
    app.dependency_overrides[auth._db_session] = session
    # Not entered as a context manager: the real lifespan (hub lock, maintenance) stays off.
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
def clear_overrides():
    yield
    from resona_api.app import app
    app.dependency_overrides.clear()


def _code(resp) -> str:
    body = resp.json()
    return body.get("error") or body["detail"]["error"]


def test_restore_revokes_every_credential_fences_leases_and_flags_missing_content(hub, capsys, clear_overrides):
    now = clock.now()
    with _session() as session:
        create_organization(session, ORG)
        later_revoked_id, later_revoked = issue_credential(session, ORG, "client")
        _, admin_token = issue_credential(session, ORG, "org_admin")
        worker_id, worker_token = enroll_worker(session, "mac", ORG)
        approve_worker(session, worker_id, private=True, capabilities=[CAND.model_dump(mode="json")])
    lease = dict(worker_id=worker_id, lease_token_hash="0" * 64, lease_until=now + timedelta(seconds=60))
    requeued = _job(JobStatus.PROCESSING, input_file=True, attempt=1, **lease)
    exhausted = _job(JobStatus.PROCESSING, input_file=True, attempt=3, **lease)
    pending_ok = _job(JobStatus.PENDING, input_file=True)
    pending_lost = _job(JobStatus.PENDING)
    done_kept = _job(JobStatus.COMPLETED, result_file=True, result_manifest="{}",
                     completed_at=now, result_expires_at=now + timedelta(hours=24))
    done_lost = _job(JobStatus.COMPLETED, result_manifest="{}",
                     completed_at=now, result_expires_at=now + timedelta(hours=24))
    done_expired = _job(JobStatus.COMPLETED, result_file=True, result_manifest="{}",
                        completed_at=now - timedelta(hours=30), result_expires_at=now - timedelta(hours=6))
    with _session() as session:
        session.add(Upload(id="upl_lost", organization_id=ORG, sha256="0" * 64, size=1,
                           media_family="audio", media_type="audio/wav",
                           expires_at=now + timedelta(minutes=30)))
        session.commit()

    backup = _backup(hub)
    # Revoked only after the backup was taken: the backup still holds it as active.
    with _session() as session:
        revoke_credential(session, later_revoked_id)

    assert main(["restore", str(backup)]) == 0
    out = capsys.readouterr().out
    for token in (later_revoked, admin_token, worker_token):
        assert token not in out
    assert "credentials_revoked: 3" in out
    assert "leases_invalidated: 2" in out

    with _session() as session:
        assert all(c.revoked_at is not None for c in session.exec(select(Credential)).all())
        assert session.get(Worker, worker_id).revoked_at is not None
        for token in (later_revoked, admin_token, worker_token):
            assert resolve_token(session, token) is None
        jobs = {job_id: session.get(Job, job_id) for job_id in
                (requeued, exhausted, pending_ok, pending_lost, done_kept, done_lost, done_expired)}
        assert session.get(Upload, "upl_lost") is None
    assert jobs[requeued].status == JobStatus.PENDING and jobs[requeued].lease_token_hash is None
    assert jobs[requeued].worker_id is None
    assert (jobs[exhausted].status, jobs[exhausted].error_code) == (JobStatus.FAILED, "attempts_exhausted")
    assert jobs[pending_ok].status == JobStatus.PENDING
    assert (jobs[pending_lost].status, jobs[pending_lost].error_code) == (JobStatus.FAILED, "content_unavailable")
    assert jobs[done_lost].status == JobStatus.COMPLETED and jobs[done_lost].error_code == "content_unavailable"
    assert not storage.job_input_path(_storage_id(), exhausted).exists()
    # Retention reapplied: the expired result file is gone.
    assert not storage.result_path(_storage_id(), done_expired).exists()

    # Old tokens are dead at the API; a freshly issued one works.
    assert _api(later_revoked).get("/v1/settings").status_code == 401
    assert _api(worker_token).post(
        "/v1/workers/claim?wait=0", json={"capabilities": []}, headers={"X-Resona-Worker-Protocol": "1"}
    ).status_code == 401
    with _session() as session:
        _, fresh = issue_credential(session, ORG, "client")
    api = _api(fresh)
    lost = api.get(f"/v1/jobs/{done_lost}/result")
    assert (lost.status_code, _code(lost)) == (410, "content_unavailable")
    view = api.get(f"/v1/jobs/{done_lost}").json()
    assert view["result_available"] is False and view["error"]["code"] == "content_unavailable"
    assert api.get(f"/v1/jobs/{pending_lost}").json()["error"]["code"] == "content_unavailable"
    kept = api.get(f"/v1/jobs/{done_kept}/result")
    assert kept.status_code == 200 and kept.json()["markdown"] == "Hallo"
    expired = api.get(f"/v1/jobs/{done_expired}/result")
    assert (expired.status_code, _code(expired)) == (410, "result_expired")


def test_restore_refuses_while_the_hub_holds_the_lock(hub):
    with _session() as session:
        create_organization(session, ORG)
        _, token = issue_credential(session, ORG, "client")
    backup = _backup(hub)

    with hublock.acquire():
        with pytest.raises(HubRunning):
            restore(backup)
        assert main(["restore", str(backup)]) == 1

    with _session() as session:
        assert resolve_token(session, token) is not None  # nothing was restored or revoked


def test_restore_refuses_a_locked_database(hub):
    with _session() as session:
        create_organization(session, ORG)
    backup = _backup(hub)
    holder = sqlite3.connect(db_engine.engine.url.database, isolation_level=None)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(RestoreError, match="locked"):
            restore(backup)
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.parametrize("content", [b"not a database", None])
def test_restore_rejects_unusable_backups(hub, content):
    backup = hub / "bad.sqlite"
    if content is not None:
        backup.write_bytes(content)
    else:
        sqlite3.connect(backup).execute("CREATE TABLE unrelated (x)").connection.close()
    with pytest.raises(RestoreError):
        restore(backup)


def test_restore_rejects_a_newer_schema(hub):
    backup = _backup(hub)
    conn = sqlite3.connect(backup)
    conn.execute("INSERT INTO schema_version (version) VALUES (999)")
    conn.commit()
    conn.close()
    with pytest.raises(RestoreError, match="newer"):
        restore(backup)


def test_restore_refuses_the_live_database_itself(hub):
    with pytest.raises(RestoreError, match="live database"):
        restore(Path(db_engine.engine.url.database))
