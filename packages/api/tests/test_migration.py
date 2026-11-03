"""Upgrading a pre-identity database: backfill, payload conversion, backup handling, refusal."""

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from resona_job_core import JobResult
from resona_api import paths, storage
from resona_api.db import schema
from resona_api.db.schema import SchemaError, migrate
from resona_api.releases import STATUS_BUNDLED, hub_release_id

PAYLOAD_COLUMNS = {"transcript", "md", "segments", "structured", "translation", "filename"}

# Mirrors the Job table as shipped before organizations existed.
LEGACY_JOB_TABLE = """
CREATE TABLE job (
    id INTEGER PRIMARY KEY,
    filename VARCHAR, upload_name VARCHAR,
    transcribed BOOLEAN, processed BOOLEAN,
    status VARCHAR, error_message VARCHAR,
    language VARCHAR, segments VARCHAR, transcript VARCHAR, model VARCHAR,
    engine VARCHAR, profile VARCHAR, profile_config VARCHAR, structured VARCHAR,
    translation VARCHAR, keepfile BOOLEAN, md VARCHAR, translate BOOLEAN,
    created_at DATETIME, updated_at DATETIME
)
"""


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_JOB_TABLE)
    raw.execute(
        "INSERT INTO job (id, filename, upload_name, transcribed, processed, status, language, "
        "segments, transcript, model, keepfile, md, translate, created_at, updated_at) "
        "VALUES (7, 'a.wav', 'Meeting.wav', 1, 1, 'completed', NULL, '', 'hallo', '', 1, "
        "'# hallo', 0, '2026-01-01 10:00:00', '2026-01-01 10:00:00')"
    )
    raw.execute(
        "INSERT INTO job (id, filename, upload_name, transcribed, processed, status, language, "
        "segments, transcript, model, keepfile, md, translate, created_at, updated_at) "
        "VALUES (9, 'b.wav', 'b.wav', 0, 0, 'pending', 'en', '', '', '', 1, '', 0, "
        "'2026-01-02 10:00:00', '2026-01-02 10:00:00')"
    )
    raw.commit()
    raw.close()
    return path


def _engine_for(path):
    return create_engine(f"sqlite:///{path}")


def test_upgrade_backfills_legacy_jobs_and_keeps_ids(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    rows = raw.execute(
        "SELECT id, organization_id, kind, private, language, status FROM job ORDER BY id"
    ).fetchall()
    org = raw.execute("SELECT id FROM organization").fetchall()
    raw.close()
    assert rows == [
        (7, "bootstrap-org", "audio", 1, "de", "completed"),
        (9, "bootstrap-org", "audio", 1, "en", "failed"),
    ]
    assert org == [("bootstrap-org",)]


def test_upgrade_converts_completed_payload_to_result_artifact(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    legacy_input = paths.FILE_PATH / "a.wav"
    legacy_input.write_bytes(b"old audio")

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    storage_id = raw.execute("SELECT storage_id FROM organization").fetchone()[0]
    columns = {row[1] for row in raw.execute("PRAGMA table_info(job)")}
    manifest, completed_at, expires_at, input_deleted = raw.execute(
        "SELECT result_manifest, completed_at, result_expires_at, input_deleted FROM job WHERE id = 7"
    ).fetchone()
    queued = raw.execute("SELECT error_code, result_manifest FROM job WHERE id = 9").fetchone()
    raw.close()

    result_file = storage.result_path(storage_id, 7)
    result = JobResult.model_validate_json(result_file.read_bytes())
    assert (result.kind, result.raw_text, result.markdown) == ("audio", "hallo", "# hallo")
    assert json.loads(manifest)["size"] == result_file.stat().st_size
    assert datetime.fromisoformat(expires_at) - datetime.fromisoformat(completed_at) == timedelta(hours=24)
    assert input_deleted == 1
    assert queued == ("legacy_unprocessed", None)
    assert not storage.result_path(storage_id, 9).exists()
    assert not columns & PAYLOAD_COLUMNS
    assert not legacy_input.exists()
    assert not (legacy_db.parent / f"legacy.db.pre-v{schema.TARGET_VERSION}.bak").exists()


def test_unverifiable_conversion_refuses_and_keeps_backup(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    legacy_input = paths.FILE_PATH / "a.wav"
    legacy_input.write_bytes(b"old audio")
    written = []

    def corrupt_writer(final, data):
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(data[: len(data) // 2])
        written.append(final)

    monkeypatch.setattr(storage, "write_bytes_atomic", corrupt_writer)

    with pytest.raises(SchemaError, match="backup is kept"):
        migrate(_engine_for(legacy_db))

    backup = sqlite3.connect(f"{legacy_db}.pre-v{schema.TARGET_VERSION}.bak")
    backup_rows = backup.execute("SELECT id, transcript FROM job ORDER BY id").fetchall()
    backup.close()
    raw = sqlite3.connect(legacy_db)
    columns = {row[1] for row in raw.execute("PRAGMA table_info(job)")}
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert backup_rows == [(7, "hallo"), (9, "")]
    assert "transcript" in columns and "organization_id" not in columns
    assert "schema_version" not in tables
    assert written and not any(path.exists() for path in written)
    assert legacy_input.exists()
    legacy_input.unlink()


def test_upgrade_refuses_legacy_jobs_without_bootstrap_org(legacy_db, monkeypatch):
    monkeypatch.delenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", raising=False)

    with pytest.raises(SchemaError, match="RESONA_BOOTSTRAP_ORGANIZATION_ID"):
        migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    count = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    raw.close()
    assert count == 2
    assert "schema_version" not in tables


def test_migrate_is_idempotent(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    migrate(_engine_for(legacy_db))

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    count = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    raw.close()
    assert count == 2


def test_v1_database_upgrades_to_releases_and_records_the_bundled_release(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:1])
    monkeypatch.setattr(schema, "TARGET_VERSION", 1)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    versions = [r[0] for r in raw.execute("SELECT version FROM schema_version ORDER BY version")]
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    release = raw.execute("SELECT release_id, status FROM release").fetchall()
    jobs = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    raw.close()
    assert versions == list(range(1, schema.TARGET_VERSION + 1))
    assert {"release", "evaluation", "baseline", "approvedreleasetuple", "uploadlink"} <= tables
    assert release == [(hub_release_id(), STATUS_BUNDLED)]
    assert jobs == 2


def test_v2_database_gains_upload_links_and_keeps_jobs(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:2])
    monkeypatch.setattr(schema, "TARGET_VERSION", 2)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()
    raw = sqlite3.connect(legacy_db)  # a v2 hub's models had no upload links
    raw.execute("DROP TABLE uploadlink")
    raw.commit()
    raw.close()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    jobs = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    raw.close()
    assert "uploadlink" in tables and version == 3 and jobs == 2



def test_engine_connections_enforce_foreign_keys_and_wal():
    from resona_api.db.engine import engine

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
