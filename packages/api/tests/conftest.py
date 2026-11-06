"""
Test configuration for resona-api.

Environment variables MUST be set before any resona_api import so that
paths.py and db/engine.py pick them up at module load time. The database is a
session-scoped temporary file migrated through ``db/schema.py``; there is no
auth-disabled mode, so tests authenticate with stored credentials.
"""
import io
import json
import os
import struct
import tempfile
import uuid
import wave
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

# ── Set env vars at module load time (before any resona_api import) ──────────
_tmp = tempfile.mkdtemp(prefix="resona_api_test_")
_db_dir = os.path.join(_tmp, "db")
_file_dir = os.path.join(_tmp, "files")
os.makedirs(_db_dir, exist_ok=True)
os.makedirs(_file_dir, exist_ok=True)

os.environ.setdefault("DATA_PATH", _tmp)
os.environ.setdefault("FILE_PATH", _file_dir)
os.environ.setdefault("DB_PATH", _db_dir)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir}/test.db")
os.environ.setdefault("RESONA_ENGINE_URL", "http://test-engine:9999")
os.environ.pop("RESONA_API_KEY", None)
os.environ.pop("RESONA_BOOTSTRAP_ORGANIZATION_ID", None)


# ── Lazy imports (after env vars are set) ────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Migrate the session database to the latest schema once."""
    from resona_api.db.engine import engine
    from resona_api.db.schema import migrate
    migrate(engine)


@pytest.fixture(autouse=True)
def clean_db(create_tables):
    """Remove job rows before each test; identities persist across the session."""
    from resona_api.db.engine import engine
    with Session(engine) as session:
        session.execute(text("DELETE FROM testbenchrun"))
        session.execute(text("DELETE FROM testbenchitem"))
        session.execute(text("DELETE FROM job"))
        session.commit()


@pytest.fixture(autouse=True)
def reset_engine_registry_cache():
    """Clear the engine_registry catalogue cache before each test."""
    from resona_api import engine_registry
    engine_registry._cache = None
    yield


def _ensure_organization(org_id: str) -> str:
    from resona_api.admin import create_organization
    from resona_api.db.engine import engine
    from resona_api.db.models import Organization
    with Session(engine) as session:
        if session.get(Organization, org_id) is None:
            create_organization(session, org_id)
            session.commit()
    return org_id


@pytest.fixture(scope="session")
def org_a() -> str:
    return _ensure_organization("org-a")


@pytest.fixture(scope="session")
def org_b() -> str:
    return _ensure_organization("org-b")


@pytest.fixture
def fresh_org() -> str:
    """A new organization with default settings, isolated from every other test."""
    from resona_api.admin import create_organization
    from resona_api.db.engine import engine

    org_id = f"org-{uuid.uuid4().hex[:12]}"
    with Session(engine) as session:
        create_organization(session, org_id)
    return org_id


@pytest.fixture
def store_profile():
    """Factory: ``store_profile(org, name, body)`` saves a new profile revision."""
    from resona_api.db.engine import engine
    from resona_api.db.models import ProfileRevision

    def make(organization_id: str, name: str, body: dict) -> int:
        with Session(engine) as session:
            existing = session.exec(
                select(ProfileRevision)
                .where(ProfileRevision.organization_id == organization_id)
                .where(ProfileRevision.name == name)
            ).all()
            revision = max((row.revision for row in existing), default=0) + 1
            session.add(ProfileRevision(
                organization_id=organization_id, name=name, revision=revision,
                profile_json=json.dumps(body), created_at=datetime.now(UTC),
            ))
            session.commit()
        return revision

    return make


@pytest.fixture
def store_settings():
    """Factory: ``store_settings(org, **changes)`` saves the next settings revision."""
    from resona_job_core import OrganizationSettings
    from resona_api.db.engine import engine
    from resona_api.db.models import SettingsRevision
    from resona_api.settings_store import current_settings

    def make(organization_id: str, **changes) -> int:
        with Session(engine) as session:
            revision, settings = current_settings(session, organization_id)
            updated = OrganizationSettings.model_validate(
                {**settings.model_dump(), **changes}
            )
            session.add(SettingsRevision(
                organization_id=organization_id, revision=revision + 1,
                settings_json=updated.model_dump_json(), created_at=datetime.now(UTC),
            ))
            session.commit()
        return revision + 1

    return make


@pytest.fixture
def client_headers():
    """Factory: ``client_headers(org_id)`` returns Bearer headers for a fresh client token."""
    from resona_api.admin import issue_credential
    from resona_api.db.engine import engine

    def make(organization_id: str) -> dict[str, str]:
        with Session(engine) as session:
            _, token = issue_credential(session, organization_id, "client")
            session.commit()
        return {"Authorization": f"Bearer {token}"}
    return make


@pytest.fixture
def admin_headers():
    """Factory: ``admin_headers(org_id)`` returns Bearer headers for a fresh org_admin token."""
    from resona_api.admin import issue_credential
    from resona_api.db.engine import engine

    def make(organization_id: str) -> dict[str, str]:
        with Session(engine) as session:
            _, token = issue_credential(session, organization_id, "org_admin")
            session.commit()
        return {"Authorization": f"Bearer {token}"}
    return make


@pytest.fixture
def operator_headers():
    """Factory: ``operator_headers(org_id)`` returns Bearer headers for a fresh hub_operator token."""
    from resona_api.admin import issue_credential
    from resona_api.db.engine import engine

    def make(organization_id: str) -> dict[str, str]:
        with Session(engine) as session:
            _, token = issue_credential(session, organization_id, "hub_operator")
            session.commit()
        return {"Authorization": f"Bearer {token}"}
    return make


@pytest.fixture
def worker_headers():
    """Factory: ``worker_headers(org_id)`` enrolls a new (unapproved) worker and returns its token."""
    from resona_api.admin import enroll_worker
    from resona_api.db.engine import engine

    def make(organization_id: str) -> dict[str, str]:
        with Session(engine) as session:
            _, token = enroll_worker(session, "test-worker", organization_id)
            session.commit()
        return {"Authorization": f"Bearer {token}", "X-Resona-Worker-Protocol": "1"}
    return make


@pytest.fixture(scope="session")
def test_app():
    """Minimal FastAPI app with the job, worker, audio, profile, settings, evaluation and admin routers (no lifespan)."""
    from resona_api.jobs_routes import router as jobs_router
    from resona_api.worker_routes import router as worker_router
    from resona_api.audio_routes import router as audio_router
    from resona_api.profiles_routes import router as profiles_router
    from resona_api.settings_routes import router as settings_router
    from resona_api.evaluations_routes import router as evaluations_router
    from resona_api.admin_routes import router as admin_router, static_router as admin_static_router
    from resona_api.testbench_routes import router as testbench_router
    app = FastAPI()
    app.include_router(jobs_router)
    app.include_router(worker_router)
    app.include_router(audio_router)
    app.include_router(profiles_router)
    app.include_router(settings_router)
    app.include_router(evaluations_router)
    app.include_router(admin_router)
    app.include_router(admin_static_router)
    app.include_router(testbench_router)
    return app


@pytest.fixture
def anon_client(test_app):
    """TestClient with no credentials, for asserting 401 on missing auth."""
    with TestClient(test_app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def client(test_app, org_a, client_headers):
    """TestClient authenticated as an org-a client; per-request headers override it."""
    with TestClient(test_app, raise_server_exceptions=True, headers=client_headers(org_a)) as c:
        yield c


@pytest.fixture
def wav_bytes() -> bytes:
    """Minimal valid WAV file (160 frames of silence, 16kHz mono)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    return buf.getvalue()
