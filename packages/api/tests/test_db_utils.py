"""Tests for resona_api.db.utils — register_job."""
import pytest
from sqlmodel import Session, select

from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus
from resona_api.db.utils import register_job


def test_register_job_creates_pending_record():
    result = register_job(filename="audio.wav", upload_name="original.wav")
    assert "id" in result
    assert result["id"] > 0
    assert "/job/" in result["result"]
    assert "audio.wav" in result["file"]

    with Session(engine) as session:
        job = session.get(Job, result["id"])
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.filename == "audio.wav"
    assert job.upload_name == "original.wav"
    assert job.keepfile is True
    assert job.translate is False


def test_register_job_keep_false():
    result = register_job(filename="f.wav", upload_name="f.wav", keep=False)
    with Session(engine) as session:
        job = session.get(Job, result["id"])
    assert job.keepfile is False


def test_register_job_translate_true():
    result = register_job(filename="f.wav", upload_name="f.wav", translate=True)
    with Session(engine) as session:
        job = session.get(Job, result["id"])
    assert job.translate is True


def test_register_job_stores_engine():
    result = register_job(filename="x.wav", upload_name="x.wav",
                          keep=True, translate=False, engine="deepgram")
    with Session(engine) as session:
        stored = session.exec(select(Job).where(Job.id == result["id"])).first()
    assert stored.engine == "deepgram"


def test_register_job_stores_profile():
    result = register_job(filename="p.wav", upload_name="p.wav", profile="medical")
    with Session(engine) as session:
        stored = session.exec(select(Job).where(Job.id == result["id"])).first()
    assert stored.profile == "medical"
