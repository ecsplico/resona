"""
Test configuration for ws-api.

Environment variables MUST be set before any ws_api import so that
paths.py and db/engine.py pick them up at module load time.
"""
import io
import os
import struct
import tempfile
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import SQLModel, Session

# ── Set env vars at module load time (before any ws_api import) ──────────────
_tmp = tempfile.mkdtemp(prefix="wsapi_test_")
_db_dir = os.path.join(_tmp, "db")
_file_dir = os.path.join(_tmp, "files")
os.makedirs(_db_dir, exist_ok=True)
os.makedirs(_file_dir, exist_ok=True)

os.environ.setdefault("DATA_PATH", _tmp)
os.environ.setdefault("FILE_PATH", _file_dir)
os.environ.setdefault("DB_PATH", _db_dir)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db_dir}/test.db")
os.environ.setdefault("WS_API_KEY", "")        # auth disabled by default
os.environ.setdefault("ENGINE_URL", "http://test-engine:9999")


# ── Lazy imports (after env vars are set) ────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all DB tables once per session."""
    from ws_api.db.engine import engine
    from ws_api.db.models import Job, Replacement, InitialPrompt  # register models
    SQLModel.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def clean_db(create_tables):
    """Truncate all tables before each test for isolation."""
    from ws_api.db.engine import engine
    with Session(engine) as session:
        session.execute(text("DELETE FROM job"))
        session.execute(text("DELETE FROM replacement"))
        session.execute(text("DELETE FROM initialprompt"))
        session.commit()


@pytest.fixture(scope="session")
def test_app():
    """Minimal FastAPI app with only the router (no lifespan/background tasks)."""
    from ws_api.endpoints import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(test_app):
    with TestClient(test_app, raise_server_exceptions=True) as c:
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
