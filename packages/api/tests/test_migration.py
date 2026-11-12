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
    assert "uploadlink" in tables and version == schema.TARGET_VERSION and jobs == 2


def _insert_job(raw) -> int:
    cursor = raw.execute(
        "INSERT INTO job (organization_id, status, created_at, updated_at) "
        "VALUES ('bootstrap-org', 'PENDING', '2026-01-03 10:00:00', '2026-01-03 10:00:00')"
    )
    raw.commit()
    return cursor.lastrowid


def _job_ddl(raw) -> str:
    return raw.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'").fetchone()[0]


def test_v4_database_gains_testbench_tables_and_keeps_jobs(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:4])
    monkeypatch.setattr(schema, "TARGET_VERSION", 4)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()
    raw = sqlite3.connect(legacy_db)  # a v4 hub's models had no testbench tables
    raw.execute("DROP TABLE testbenchrun")
    raw.execute("DROP TABLE testbenchitem")
    raw.commit()
    raw.close()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = [r[0] for r in raw.execute("SELECT version FROM schema_version ORDER BY version")]
    jobs = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    run_ddl = raw.execute("SELECT sql FROM sqlite_master WHERE name='testbenchrun'").fetchone()[0]
    item_ddl = raw.execute("SELECT sql FROM sqlite_master WHERE name='testbenchitem'").fetchone()[0]
    raw.close()
    assert {"testbenchitem", "testbenchrun"} <= tables
    assert versions == list(range(1, schema.TARGET_VERSION + 1)) and schema.TARGET_VERSION >= 5
    assert jobs == 2
    assert "ON DELETE CASCADE" in run_ddl
    assert "AUTOINCREMENT" in run_ddl and "AUTOINCREMENT" in item_ddl


def test_v6_database_gains_worker_diarize_flag_off_for_existing_workers(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:6])
    monkeypatch.setattr(schema, "TARGET_VERSION", 6)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()
    raw = sqlite3.connect(legacy_db)  # a v6 hub's models had no worker.diarize
    raw.execute("ALTER TABLE worker DROP COLUMN diarize")
    raw.execute(
        "INSERT INTO worker (id, organization_id, name, approved, approved_private, approved_capabilities, "
        "reported_capabilities, text_jobs) VALUES ('w1', 'bootstrap-org', 'gpu-box', 1, 1, '[]', '[]', 1)"
    )
    raw.commit()
    raw.close()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    row = raw.execute("SELECT text_jobs, diarize FROM worker WHERE id = 'w1'").fetchone()
    jobs = raw.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    raw.close()
    assert version == schema.TARGET_VERSION >= 7
    assert row == (1, 0) and jobs == 2


def test_v7_database_gains_unreported_worker_features(legacy_db, monkeypatch):
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:7])
    monkeypatch.setattr(schema, "TARGET_VERSION", 7)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()
    raw = sqlite3.connect(legacy_db)  # a v7 hub's models had no worker.features
    raw.execute("ALTER TABLE worker DROP COLUMN features")
    raw.execute(
        "INSERT INTO worker (id, organization_id, name, approved, approved_private, approved_capabilities, "
        "reported_capabilities, runtime_version) VALUES ('w1', 'bootstrap-org', 'gpu-box', 1, 1, '[]', '[]', '0.1.0')"
    )
    raw.commit()
    raw.close()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    row = raw.execute("SELECT runtime_version, features FROM worker WHERE id = 'w1'").fetchone()
    raw.close()
    assert version == schema.TARGET_VERSION >= 8
    assert row == ("0.1.0", None)  # judged by its release until it reports



def test_v3_database_never_reissues_a_deleted_job_id(legacy_db, monkeypatch):
    from resona_api.db.models import Job

    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    monkeypatch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:3])
    monkeypatch.setattr(schema, "TARGET_VERSION", 3)
    # A v3 hub created job with a plain INTEGER PRIMARY KEY.
    monkeypatch.setitem(Job.__table__.dialect_options["sqlite"], "autoincrement", False)
    migrate(_engine_for(legacy_db))
    monkeypatch.undo()
    monkeypatch.setenv("RESONA_BOOTSTRAP_ORGANIZATION_ID", "bootstrap-org")
    raw = sqlite3.connect(legacy_db)
    assert "AUTOINCREMENT" not in _job_ddl(raw)
    raw.execute("CREATE INDEX ix_job_worker_id ON job (worker_id)")
    raw.execute(
        "INSERT INTO upload (id, organization_id, sha256, size, media_family, media_type, "
        "created_at, expires_at, consumed_by_job_id) VALUES ('upl_1', 'bootstrap-org', 'x', 1, "
        "'audio', 'audio/wav', '2026-01-03 10:00:00', '2026-01-03 11:00:00', 12)"
    )
    raw.execute(
        "INSERT INTO idempotencyrecord (organization_id, key, request_hash, job_id, created_at) "
        "VALUES ('bootstrap-org', 'k', 'h', 11, '2026-01-03 10:00:00')"
    )
    raw.execute("DELETE FROM job WHERE id = 9")
    raw.commit()
    before = raw.execute("SELECT * FROM job ORDER BY id").fetchall()
    raw.close()

    migrate(_engine_for(legacy_db))

    raw = sqlite3.connect(legacy_db)
    after = raw.execute("SELECT * FROM job ORDER BY id").fetchall()
    ddl = _job_ddl(raw)
    indexes = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='job'")}
    version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    first = _insert_job(raw)
    raw.execute("DELETE FROM job WHERE id = ?", (first,))
    raw.commit()
    second = _insert_job(raw)
    raw.close()
    assert after == before and [r[0] for r in after] == [7]
    assert "AUTOINCREMENT" in ddl and "ix_job_worker_id" in indexes
    assert version == schema.TARGET_VERSION
    assert (first, second) == (13, 14)


def test_fresh_database_does_not_reuse_deleted_job_ids(tmp_path):
    path = tmp_path / "fresh.db"
    migrate(_engine_for(path))
    raw = sqlite3.connect(path)
    raw.execute(
        "INSERT INTO organization (id, storage_id, created_at) "
        "VALUES ('bootstrap-org', 's', '2026-01-03 10:00:00')"
    )
    raw.commit()
    assert "AUTOINCREMENT" in _job_ddl(raw)
    _insert_job(raw)
    last = _insert_job(raw)
    raw.execute("DELETE FROM job WHERE id = ?", (last,))
    raw.commit()
    reissued = _insert_job(raw)
    raw.close()
    assert (last, reissued) == (2, 3)




def test_engine_connections_enforce_foreign_keys_and_wal():
    from resona_api.db.engine import engine

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
