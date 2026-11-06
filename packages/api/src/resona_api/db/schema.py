"""Versioned SQLite schema for resona-api.

Schema changes are ordered, numbered migrations. ``migrate(engine)`` applies
every migration newer than the recorded ``schema_version`` inside one
``BEGIN EXCLUSIVE`` transaction, after copying an existing database file to
``<dbfile>.pre-v<target>.bak`` with the SQLite backup API. The backup guards
the upgrade only: a failed upgrade keeps it, a committed upgrade deletes it,
because it holds content (legacy transcripts) that retention must not outlive.

To add a schema change: append a new ``_vN_<name>(conn)`` function that takes a
raw :class:`sqlite3.Connection`, then append ``(N, _vN_<name>)`` to
:data:`MIGRATIONS`. A migration verifies its own work before returning (raise
:class:`SchemaError` to roll back) and may return a callable that runs only
after the commit, for irreversible filesystem cleanup. NEVER edit a migration
that has shipped and NEVER insert one before an existing entry; later
migrations (2, 3, ...) are appended. Table definitions for new tables come from
the ORM metadata in ``models.py``; any change to an existing table must be an
explicit ``ALTER`` or table rebuild so legacy rows survive.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from decouple import config
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel

from resona_job_core import JobResult, Provenance, Segment, default_settings

from .. import clock, storage
from ..paths import FILE_PATH
from .models import Job, Organization, SettingsRevision

log = logging.getLogger(__name__)

AfterCommit = Callable[[], None]
MigrationFn = Callable[[sqlite3.Connection], AfterCommit | None]

# Same lifetime the broker gives a freshly completed result.
LEGACY_RESULT_TTL = timedelta(hours=24)
_TERMINAL = ("completed", "failed", "cancelled")


class SchemaError(RuntimeError):
    """The database cannot be migrated safely; startup must refuse."""


# ── helpers ─────────────────────────────────────────────────────────────

def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _naive_utc_iso(value: datetime | None = None) -> str:
    """UTC timestamp in the text form SQLAlchemy's SQLite DateTime reads back."""
    value = value if value is not None else clock.now()
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(" ")


def _create_missing_tables(conn: sqlite3.Connection) -> None:
    """Create every ORM table not yet present, dependencies first."""
    existing = _table_names(conn)
    dialect = sqlite_dialect.dialect()
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing:
            conn.execute(str(CreateTable(table).compile(dialect=dialect)))


# ── migration 1: identity, settings, profiles; job gains org columns ───
#
# Legacy payload columns (transcript, md, segments, structured, translation) do
# not survive into the new ``job`` table. Completed rows become result artifacts
# under the bootstrap organization with the normal 24-hour result lifetime.
# Every legacy row ends terminal: queued work has no approved execution snapshot
# and no input in the new layout, so it fails as ``legacy_unprocessed`` and must
# be resubmitted. Legacy input files under ``FILE_PATH`` are deleted after commit.

def _legacy_segments(raw: Any) -> list[Segment]:
    try:
        items = json.loads(raw) if isinstance(raw, str) and raw.strip() else []
    except ValueError:
        return []
    segments: list[Segment] = []
    for item in items if isinstance(items, list) else []:
        try:
            segments.append(Segment(start=item["start"], end=item["end"], text=item["text"]))
        except (KeyError, TypeError, ValidationError):
            continue
    return segments


def _legacy_structured(raw: Any) -> dict[str, object]:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _legacy_result(rec: dict[str, Any]) -> JobResult | None:
    """Build the result artifact for a completed legacy row, or ``None`` if it has no payload."""
    transcript = rec.get("transcript") or ""
    markdown = rec.get("md") or ""
    segments = _legacy_segments(rec.get("segments"))
    structured = _legacy_structured(rec.get("structured"))
    translation = rec.get("translation") or ""
    if translation and "translation" not in structured:
        structured["translation"] = translation
    if not (transcript or markdown or segments or structured):
        return None
    # StageProvenance needs an accelerator and timings, which legacy rows never
    # recorded; engine/model go into a warning instead of invented stage data.
    origin = f"engine={rec.get('engine') or 'unknown'} model={rec.get('model') or 'unknown'}"
    return JobResult(
        kind="audio",
        raw_text=transcript,
        markdown=markdown or transcript,
        language=rec.get("language") or "de",
        segments=segments,
        structured=structured,
        warnings=[f"legacy_import: {origin}; accelerator and timings not recorded"],
        provenance=Provenance(),
    )


def _legacy_input(filename: Any) -> Path | None:
    """The legacy input path for a stored generated filename; never escapes ``FILE_PATH``."""
    if not isinstance(filename, str) or not filename:
        return None
    name = Path(filename).name
    if name != filename or name in (".", ".."):
        return None
    return FILE_PATH / name


def _verify_results(expected: dict[Path, str]) -> None:
    for path, sha256 in expected.items():
        try:
            data = path.read_bytes()
            JobResult.model_validate_json(data)
        except (OSError, ValidationError) as exc:
            raise SchemaError(
                "Converted legacy results could not be verified; the pre-migration "
                "backup is kept and the database is unchanged."
            ) from exc
        if hashlib.sha256(data).hexdigest() != sha256:
            raise SchemaError(
                "Converted legacy result does not match its manifest; the pre-migration "
                "backup is kept and the database is unchanged."
            )


def _convert_legacy_rows(conn: sqlite3.Connection, storage_id: str) -> list[Path]:
    """Write result artifacts, make every migrated row terminal; return legacy inputs to delete."""
    now = clock.now()
    stamp = _naive_utc_iso(now)
    expires = _naive_utc_iso(now + LEGACY_RESULT_TTL)
    cursor = conn.execute("SELECT * FROM job_legacy")
    names = [d[0] for d in cursor.description]
    written: dict[Path, str] = {}
    inputs: list[Path] = []
    try:
        for row in cursor.fetchall():
            rec = dict(zip(names, row))
            job_id = int(rec["id"])
            status = str(rec.get("status") or "").lower()
            legacy_input = _legacy_input(rec.get("filename"))
            if legacy_input is not None:
                inputs.append(legacy_input)

            if status == "completed":
                manifest = None
                result_expires = stamp  # nothing to serve: already expired
                result = _legacy_result(rec)
                if result is not None:
                    data = result.model_dump_json().encode()
                    sha256 = hashlib.sha256(data).hexdigest()
                    path = storage.result_path(storage_id, job_id)
                    storage.write_bytes_atomic(path, data)
                    written[path] = sha256
                    manifest = json.dumps(
                        {"sha256": sha256, "size": len(data), "attempt": 0,
                         "worker_id": None, "lease_token_hash": None, "source": "legacy"},
                        sort_keys=True,
                    )
                    result_expires = expires
                conn.execute(
                    "UPDATE job SET result_manifest = ?, completed_at = ?, result_expires_at = ?, "
                    "input_deleted = 1, updated_at = ? WHERE id = ?",
                    (manifest, stamp, result_expires, stamp, job_id),
                )
            elif status in _TERMINAL:
                conn.execute(
                    "UPDATE job SET completed_at = ?, input_deleted = 1, updated_at = ? WHERE id = ?",
                    (stamp, stamp, job_id),
                )
            else:
                conn.execute(
                    "UPDATE job SET status = 'failed', error_code = 'legacy_unprocessed', "
                    "error_message = 'queued before the upgrade; resubmit the input', "
                    "completed_at = ?, input_deleted = 1, updated_at = ? WHERE id = ?",
                    (stamp, stamp, job_id),
                )
        _verify_results(written)
    except BaseException:
        for path in written:
            storage.remove_file(path)
        raise
    log.info("Converted %d legacy result(s) to artifacts", len(written))
    return inputs


def _v1_identity_settings_jobs(conn: sqlite3.Connection) -> AfterCommit | None:
    has_legacy_job = "job" in _table_names(conn)
    legacy_rows = 0
    if has_legacy_job:
        legacy_rows = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]

    bootstrap_org = config("RESONA_BOOTSTRAP_ORGANIZATION_ID", default="").strip()
    if legacy_rows and not bootstrap_org:
        raise SchemaError(
            f"{legacy_rows} existing job row(s) need an owning organization. "
            "Set RESONA_BOOTSTRAP_ORGANIZATION_ID to the organization that owns them "
            "and start again."
        )

    if has_legacy_job:
        conn.execute("ALTER TABLE job RENAME TO job_legacy")

    _create_missing_tables(conn)

    if not has_legacy_job:
        return None

    legacy_cols = [r[1] for r in conn.execute("PRAGMA table_info(job_legacy)")]
    if legacy_rows:
        conn.execute(
            f"INSERT OR IGNORE INTO {Organization.__table__.name} (id, storage_id, created_at) "
            "VALUES (?, ?, ?)",
            (bootstrap_org, uuid.uuid4().hex, _naive_utc_iso()),
        )
        conn.execute(
            f"INSERT OR IGNORE INTO {SettingsRevision.__table__.name} "
            "(organization_id, revision, settings_json, created_at) VALUES (?, 1, ?, ?)",
            (bootstrap_org, default_settings().model_dump_json(), _naive_utc_iso()),
        )

    insert_cols: list[str] = []
    select_exprs: list[str] = []
    params: list[Any] = []
    for col in (c.name for c in Job.__table__.columns):
        if col == "organization_id":
            insert_cols.append(col)
            select_exprs.append("?")
            params.append(bootstrap_org)
        elif col == "kind":
            insert_cols.append(col)
            select_exprs.append("'audio'")
        elif col == "private":
            insert_cols.append(col)
            select_exprs.append("1")
        elif col == "language":
            insert_cols.append(col)
            if "language" in legacy_cols:
                select_exprs.append("COALESCE(NULLIF(language, ''), 'de')")
            else:
                select_exprs.append("'de'")
        elif col in legacy_cols:
            insert_cols.append(col)
            select_exprs.append(col)

    conn.execute(
        f"INSERT INTO job ({', '.join(insert_cols)}) "
        f"SELECT {', '.join(select_exprs)} FROM job_legacy",
        params,
    )
    inputs: list[Path] = []
    if legacy_rows:
        storage_id = conn.execute(
            f"SELECT storage_id FROM {Organization.__table__.name} WHERE id = ?", (bootstrap_org,)
        ).fetchone()[0]
        inputs = _convert_legacy_rows(conn, storage_id)
    conn.execute("DROP TABLE job_legacy")
    log.info("Migrated %d legacy job row(s)", legacy_rows)

    def _remove_legacy_inputs() -> None:
        for path in inputs:
            storage.remove_file(path)

    return _remove_legacy_inputs if inputs else None


# ── migration 2: releases, evaluations, baselines, approved release tuples ─
#
# Records the hub's own (bundled) release so it can run evaluation jobs without
# being staged. Production jobs of any release, the bundled one included, need a
# promoted evaluation of their (execution profile, platform, release). On a fresh
# database migration 1 already created these tables from the ORM metadata, so
# creation is idempotent.

_V2_TABLES = ("release", "evaluation", "baseline", "approvedreleasetuple")


def _v2_releases_evaluations(conn: sqlite3.Connection) -> AfterCommit | None:
    from ..releases import STATUS_BUNDLED, hub_release_id

    _create_missing_tables(conn)
    missing = set(_V2_TABLES) - _table_names(conn)
    if missing:
        raise SchemaError(f"migration 2 did not create tables: {sorted(missing)}")
    release_id = hub_release_id()
    body = json.dumps({"release_id": release_id, "bundled": True}, sort_keys=True)
    conn.execute(
        "INSERT OR IGNORE INTO release (release_id, status, manifest_json, manifest_sha256, staged_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (release_id, STATUS_BUNDLED, body, hashlib.sha256(body.encode()).hexdigest(), _naive_utc_iso()),
    )
    return None


# ── migration 3: single-use upload links ────────────────────────────────

def _v3_upload_links(conn: sqlite3.Connection) -> AfterCommit | None:
    _create_missing_tables(conn)
    if "uploadlink" not in _table_names(conn):
        raise SchemaError("migration 3 did not create table uploadlink")
    return None


# ── migration 4: job ids are never reused ───────────────────────────────
#
# A plain ``INTEGER PRIMARY KEY`` hands the highest id out again once that row
# is deleted, so a client holding a stale job id could read a newer job. The
# ``job`` table is rebuilt with AUTOINCREMENT from the ORM metadata (refusing if
# its columns or foreign keys differ from the table on disk), its indexes are
# recreated from their stored SQL, and ``sqlite_sequence`` is seeded with the
# highest job id referenced anywhere, so ids already handed out (and possibly
# deleted) are never issued again. On a fresh database migration 1 already
# created ``job`` with AUTOINCREMENT; only the seeding runs.

# Every (table, column) besides ``job.id`` that holds a job id.
_JOB_ID_REFERENCES = (("upload", "consumed_by_job_id"), ("idempotencyrecord", "job_id"))


def _job_has_autoincrement(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'job'").fetchone()
    return row is not None and "AUTOINCREMENT" in row[0].upper()


def _table_shape(conn: sqlite3.Connection, table: str) -> tuple[list[tuple], list[tuple]]:
    columns = [tuple(r)[1:] for r in conn.execute(f"PRAGMA table_info({table})")]
    foreign_keys = [tuple(r)[2:] for r in conn.execute(f"PRAGMA foreign_key_list({table})")]
    return columns, foreign_keys


def _v4_job_autoincrement(conn: sqlite3.Connection) -> AfterCommit | None:
    rows = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    if not _job_has_autoincrement(conn):
        old_shape = _table_shape(conn, "job")
        index_sql = [r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'job' AND sql IS NOT NULL"
        )]
        conn.execute("ALTER TABLE job RENAME TO job_v4_old")
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'job_v4_old' "
            "AND sql IS NOT NULL"
        ).fetchall():
            conn.execute(f'DROP INDEX "{name}"')
        conn.execute(str(CreateTable(Job.__table__).compile(dialect=sqlite_dialect.dialect())))
        if _table_shape(conn, "job") != old_shape:
            raise SchemaError(
                "migration 4: the job table on disk does not match the model; refusing to rebuild it"
            )
        for sql in index_sql:
            conn.execute(sql)
        columns = ", ".join(c.name for c in Job.__table__.columns)
        conn.execute(f"INSERT INTO job ({columns}) SELECT {columns} FROM job_v4_old")
        conn.execute("DROP TABLE job_v4_old")

    tables = _table_names(conn)
    highest = conn.execute("SELECT COALESCE(MAX(id), 0) FROM job").fetchone()[0]
    for table, column in _JOB_ID_REFERENCES:
        if table in tables:
            value = conn.execute(f"SELECT COALESCE(MAX({column}), 0) FROM {table}").fetchone()[0]
            highest = max(highest, int(value))
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'job'").fetchone()
    if seq is None:
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('job', ?)", (highest,))
    elif int(seq[0]) < highest:
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'job'", (highest,))

    if not _job_has_autoincrement(conn):
        raise SchemaError("migration 4 did not make job.id AUTOINCREMENT")
    if conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] != rows:
        raise SchemaError("migration 4 lost job rows")
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'job'").fetchone()
    if seq is None or int(seq[0]) < highest:
        raise SchemaError("migration 4 did not seed the job id sequence")
    return None


# ── migration 5: testbench items and runs ───────────────────────────────

_V5_TABLES = ("testbenchitem", "testbenchrun")


def _v5_testbench(conn: sqlite3.Connection) -> AfterCommit | None:
    _create_missing_tables(conn)
    missing = set(_V5_TABLES) - _table_names(conn)
    if missing:
        raise SchemaError(f"migration 5 did not create tables: {sorted(missing)}")
    return None


# Ordered. Append new migrations; never reorder or edit shipped ones.
MIGRATIONS: list[tuple[int, MigrationFn]] = [
    (1, _v1_identity_settings_jobs),
    (2, _v2_releases_evaluations),
    (3, _v3_upload_links),
    (4, _v4_job_autoincrement),
    (5, _v5_testbench),
]

TARGET_VERSION: int = MIGRATIONS[-1][0]


# ── runner ──────────────────────────────────────────────────────────────

def _current_version(conn: sqlite3.Connection) -> int:
    if "schema_version" not in _table_names(conn):
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(engine) -> None:
    """Bring the database behind ``engine`` up to :data:`TARGET_VERSION`.

    Idempotent: a database already at the target version is left untouched.
    The pre-migration backup survives a failed upgrade and is deleted after a
    committed one.
    """
    db_path = engine.url.database
    backup_path: Path | None = None
    after_commit: list[AfterCommit] = []
    raw = sqlite3.connect(db_path or ":memory:", isolation_level=None, timeout=5)
    try:
        if _current_version(raw) >= TARGET_VERSION:
            return

        if db_path and _table_names(raw):
            backup_path = Path(f"{db_path}.pre-v{TARGET_VERSION}.bak")
            dst = sqlite3.connect(backup_path)
            try:
                raw.backup(dst)
            finally:
                dst.close()
            log.info("Backed up database before migration to %s", backup_path)

        raw.execute("BEGIN EXCLUSIVE")
        try:
            current = _current_version(raw)  # re-check under the lock
            raw.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            for version, fn in MIGRATIONS:
                if version <= current:
                    continue
                log.info("Applying schema migration %d", version)
                action = fn(raw)
                if action is not None:
                    after_commit.append(action)
                raw.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            raw.execute("COMMIT")
        except BaseException:
            raw.execute("ROLLBACK")
            raise
    finally:
        raw.close()

    for action in after_commit:
        action()
    if backup_path is not None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{backup_path}{suffix}").unlink(missing_ok=True)
        log.info("Removed pre-migration backup after a verified upgrade")
