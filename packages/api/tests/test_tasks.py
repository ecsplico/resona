"""Tests for resona_api.tasks_transcribe.TranscribeTask._process_next_job.

Key differences from ws-api tests:
- Engine returns {text, language, segments} — no 'md' key (postprocessing is local)
- Replacements are applied locally via PostprocessPipeline, NOT sent to the engine
- job.transcript holds the raw engine text
- job.md holds the postprocessed text (with replacements applied)
- engine_registry.run_stt() is called WITHOUT replacements parameter
"""
import os
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from resona_api import engine_registry as reg
from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus
from resona_api.db.utils import register_job
from resona_api.tasks_transcribe import TranscribeTask

FILE_PATH = Path(os.environ.get("FILE_PATH", "/tmp"))

# Engine result has no 'md' key — postprocessing is done locally
ASR_RESULT = {
    "text": "raw text",
    "language": "de",
    "segments": [],
}

_FAKE_INFO = reg.EngineInfo(
    name="test-engine",
    kind="local",
    capabilities=["stt"],
    private=True,
    available=True,
    models=[],
    url="http://test-engine:9999",
)


def make_task() -> TranscribeTask:
    return TranscribeTask(shutdown_event=Event())


def write_audio_file(job_filename: str) -> Path:
    p = FILE_PATH / job_filename
    p.write_bytes(b"RIFF")
    return p


def test_no_job_does_nothing():
    task = make_task()
    with patch.object(reg, "resolve") as resolve, \
         patch.object(reg, "run_stt") as run_stt:
        task._process_next_job()
        resolve.assert_not_called()
        run_stt.assert_not_called()


def test_pending_job_becomes_completed():
    result = register_job("audio.wav", "original.wav")
    job_id = result["id"]
    write_audio_file("audio.wav")

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=ASR_RESULT),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED


def test_missing_audio_file_marks_failed():
    result = register_job("missing.wav", "missing.wav")
    job_id = result["id"]
    # Don't create the file

    task = make_task()
    with (
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.FAILED
    assert "File not found" in (job.error_message or "")


def test_engine_error_marks_failed():
    result = register_job("audio2.wav", "audio2.wav")
    job_id = result["id"]
    write_audio_file("audio2.wav")

    task = make_task()

    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", side_effect=RuntimeError("engine exploded")),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.FAILED
    assert "engine exploded" in (job.error_message or "")


def test_run_stt_called_without_replacements():
    """run_stt must NOT receive replacements — they are applied locally."""
    result = register_job("audio3.wav", "audio3.wav")
    write_audio_file("audio3.wav")

    replacements = [{"name": "foo", "replacement": "bar"}]
    prompt = "my prompt"

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=ASR_RESULT) as mock_run_stt,
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=replacements),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=prompt),
    ):
        task._process_next_job()

    mock_run_stt.assert_called_once()
    _, kwargs = mock_run_stt.call_args
    # Replacements must NOT be passed to the engine
    assert "replacements" not in kwargs
    # prompt should still be passed
    assert kwargs["prompt"] == prompt


def test_transcript_and_md_set_correctly():
    """job.transcript = raw engine text; job.md = postprocessed with replacements."""
    result = register_job("audio4.wav", "audio4.wav")
    job_id = result["id"]
    write_audio_file("audio4.wav")

    # Engine returns raw text without replacements applied
    raw_result = {"text": "foo baz", "language": "de", "segments": []}

    # Replacement: "foo" -> "bar"
    replacements = [{"name": "foo", "replacement": "bar"}]

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=raw_result),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=replacements),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)

    assert job.status == JobStatus.COMPLETED
    # Raw transcript from engine
    assert job.transcript == "foo baz"
    # Postprocessed md with replacement applied
    assert job.md == "bar baz"


def test_md_equals_transcript_when_no_replacements():
    """When no replacements are configured, md should equal the raw transcript."""
    result = register_job("audio5.wav", "audio5.wav")
    job_id = result["id"]
    write_audio_file("audio5.wav")

    raw_result = {"text": "hello world", "language": "de", "segments": []}

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=raw_result),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)

    assert job.status == JobStatus.COMPLETED
    assert job.transcript == "hello world"
    assert job.md == "hello world"


def test_processes_oldest_job_first():
    r1 = register_job("old.wav", "old.wav")
    r2 = register_job("new.wav", "new.wav")
    write_audio_file("old.wav")
    write_audio_file("new.wav")

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=ASR_RESULT),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        j1 = session.get(Job, r1["id"])
        j2 = session.get(Job, r2["id"])
    assert j1.status == JobStatus.COMPLETED
    assert j2.status == JobStatus.PENDING
