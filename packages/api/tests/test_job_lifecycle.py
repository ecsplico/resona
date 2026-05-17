"""End-to-end job lifecycle: submit → process → retrieve with postprocessing.

Uses the shared test DB from conftest.py (env vars are set at module load time
before any resona_api import).
"""
import io
import struct
import wave
from threading import Event
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from resona_api import engine_registry as reg
from resona_api.db.engine import engine as db_engine
from resona_api.db.models import Replacement
from resona_api.db.utils import register_job
from resona_api.tasks_transcribe import TranscribeTask


def _make_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * 160, *([0] * 160)))
    return buf.getvalue()


_FAKE_INFO = reg.EngineInfo(
    name="test-engine",
    kind="local",
    capabilities=["stt"],
    private=True,
    available=True,
    models=[],
    url="http://test-engine:9999",
)


@pytest.fixture
def lifecycle_client(client):
    """Reuse the shared test client from conftest."""
    return client


def test_full_job_lifecycle_with_postprocessing(lifecycle_client, clean_db):
    """Submit job, manually run task processing, verify transcript and md."""
    import os
    from pathlib import Path

    file_path = Path(os.environ.get("FILE_PATH", "/tmp"))

    # 1. Add a replacement rule directly to DB
    with Session(db_engine) as session:
        session.add(Replacement(name="hello", replacement="GOODBYE", active=True))
        session.commit()

    # 2. Submit a job via the endpoint
    wav = _make_wav_bytes()
    resp = lifecycle_client.post(
        "/jobs",
        files=[("audio_files", ("test.wav", wav, "audio/wav"))],
    )
    assert resp.status_code == 200
    job_data = resp.json()
    assert len(job_data) == 1
    job_id = job_data[0]["id"]

    # 3. Verify job is PENDING
    resp = lifecycle_client.get(f"/job/{job_id}")
    assert resp.json()["status"] == "pending"

    # 4. Write a dummy audio file so the task can find it
    from resona_api.db.engine import engine
    from resona_api.db.models import Job
    from sqlmodel import Session as S

    with S(engine) as s:
        job = s.get(Job, job_id)
        audio_file = file_path / job.filename
    audio_file.write_bytes(_make_wav_bytes())

    # 5. Process the job via the registry
    asr_result = {"text": "hello world", "language": "de", "segments": []}

    task = TranscribeTask(shutdown_event=Event())
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=asr_result) as mock_run_stt,
        patch("resona_api.tasks_transcribe.write_md_file"),
    ):
        task._process_next_job()

    # 6. Verify job is COMPLETED with correct fields
    resp = lifecycle_client.get(f"/job/{job_id}")
    job = resp.json()
    assert job["status"] == "completed"
    assert job["transcript"] == "hello world"   # raw text from engine
    assert job["md"] == "GOODBYE world"          # postprocessed with replacement

    # 7. Verify run_stt was called WITHOUT replacements
    mock_run_stt.assert_called_once()
    _, kwargs = mock_run_stt.call_args
    assert "replacements" not in kwargs


def test_job_lifecycle_no_replacements(lifecycle_client, clean_db):
    """When no replacements are active, md equals the raw transcript."""
    import os
    from pathlib import Path

    file_path = Path(os.environ.get("FILE_PATH", "/tmp"))

    wav = _make_wav_bytes()
    resp = lifecycle_client.post(
        "/jobs",
        files=[("audio_files", ("test.wav", wav, "audio/wav"))],
    )
    assert resp.status_code == 200
    job_id = resp.json()[0]["id"]

    from resona_api.db.engine import engine
    from resona_api.db.models import Job
    from sqlmodel import Session as S

    with S(engine) as s:
        job = s.get(Job, job_id)
        audio_file = file_path / job.filename
    audio_file.write_bytes(_make_wav_bytes())

    asr_result = {"text": "plain text output", "language": "de", "segments": []}

    task = TranscribeTask(shutdown_event=Event())
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=asr_result),
        patch("resona_api.tasks_transcribe.write_md_file"),
    ):
        task._process_next_job()

    resp = lifecycle_client.get(f"/job/{job_id}")
    job = resp.json()
    assert job["status"] == "completed"
    assert job["transcript"] == "plain text output"
    assert job["md"] == "plain text output"
