"""Tests for resona_api.db.utils — register_job, get_active_replacements, etc."""
import pytest
from sqlmodel import Session

from resona_api.db.engine import engine
from resona_api.db.models import Job, Replacement, InitialPrompt, JobStatus
from resona_api.db.utils import (
    register_job,
    get_active_replacements,
    get_active_initial_prompts_string,
)


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


def test_get_active_replacements_empty():
    result = get_active_replacements()
    assert result == []


def test_get_active_replacements_returns_active_only():
    with Session(engine) as session:
        session.add(Replacement(name="active", replacement="yes", active=True))
        session.add(Replacement(name="inactive", replacement="no", active=False))
        session.commit()

    result = get_active_replacements()
    assert len(result) == 1
    assert result[0] == {"name": "active", "replacement": "yes"}


def test_get_active_replacements_ordered_by_id():
    with Session(engine) as session:
        session.add(Replacement(name="first", replacement="1", active=True))
        session.add(Replacement(name="second", replacement="2", active=True))
        session.commit()

    result = get_active_replacements()
    assert [r["name"] for r in result] == ["first", "second"]


def test_get_active_initial_prompts_string_empty():
    result = get_active_initial_prompts_string()
    assert result == ""


def test_get_active_initial_prompts_string_single():
    with Session(engine) as session:
        session.add(InitialPrompt(phrase="hello whisper", active=True))
        session.commit()
    assert get_active_initial_prompts_string() == "hello whisper"


def test_get_active_initial_prompts_string_multiple():
    with Session(engine) as session:
        session.add(InitialPrompt(phrase="one", active=True))
        session.add(InitialPrompt(phrase="two", active=True))
        session.add(InitialPrompt(phrase="inactive", active=False))
        session.commit()
    result = get_active_initial_prompts_string()
    assert "one" in result
    assert "two" in result
    assert "inactive" not in result


def test_get_active_initial_prompts_string_only_inactive():
    with Session(engine) as session:
        session.add(InitialPrompt(phrase="off", active=False))
        session.commit()
    assert get_active_initial_prompts_string() == ""


def test_register_job_stores_engine():
    from resona_api.db.utils import register_job
    from resona_api.db.models import Job
    from resona_api.db.engine import engine
    from sqlmodel import Session, select

    result = register_job(filename="x.wav", upload_name="x.wav",
                          keep=True, translate=False, engine="deepgram")
    with Session(engine) as session:
        stored = session.exec(select(Job).where(Job.id == result["id"])).first()
    assert stored.engine == "deepgram"
