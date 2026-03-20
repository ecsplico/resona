"""Tests for ws_api.tasks_transcribe.TranscribeTask._process_next_job."""
import os
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from ws_api.db.engine import engine
from ws_api.db.models import Job, JobStatus
from ws_api.db.utils import register_job
from ws_api.engine_client import EngineClient
from ws_api.tasks_transcribe import TranscribeTask

FILE_PATH = Path(os.environ.get("FILE_PATH", "/tmp"))

ASR_RESULT = {
    "text": "hello",
    "language": "de",
    "segments": [],
    "md": "hello",
}


def make_task(engine_client=None) -> TranscribeTask:
    if engine_client is None:
        engine_client = MagicMock(spec=EngineClient)
        engine_client.transcribe.return_value = ASR_RESULT
    return TranscribeTask(shutdown_event=Event(), engine_client=engine_client)


def write_audio_file(job_filename: str) -> Path:
    p = FILE_PATH / job_filename
    p.write_bytes(b"RIFF")
    return p


def test_no_job_does_nothing():
    task = make_task()
    task._process_next_job()
    task.engine_client.transcribe.assert_not_called()


def test_pending_job_becomes_completed():
    result = register_job("audio.wav", "original.wav")
    job_id = result["id"]
    write_audio_file("audio.wav")

    task = make_task()
    with (
        patch("ws_api.tasks_transcribe.write_md_file"),
        patch("ws_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("ws_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("ws_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
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
        patch("ws_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("ws_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
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

    failing_client = MagicMock(spec=EngineClient)
    failing_client.transcribe.side_effect = RuntimeError("engine exploded")
    task = make_task(engine_client=failing_client)

    with (
        patch("ws_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("ws_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.FAILED
    assert "engine exploded" in (job.error_message or "")


def test_engine_client_called_with_replacements_and_prompt():
    result = register_job("audio3.wav", "audio3.wav")
    write_audio_file("audio3.wav")
    job_id = result["id"]

    mock_client = MagicMock(spec=EngineClient)
    mock_client.transcribe.return_value = ASR_RESULT
    task = make_task(engine_client=mock_client)

    replacements = [{"name": "foo", "replacement": "bar"}]
    prompt = "my prompt"

    with (
        patch("ws_api.tasks_transcribe.write_md_file"),
        patch("ws_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("ws_api.tasks_transcribe.get_active_replacements", return_value=replacements),
        patch("ws_api.tasks_transcribe.get_active_initial_prompts_string", return_value=prompt),
    ):
        task._process_next_job()

    mock_client.transcribe.assert_called_once()
    _, kwargs = mock_client.transcribe.call_args
    assert kwargs["replacements"] == replacements
    assert kwargs["initial_prompt"] == prompt


def test_processes_oldest_job_first():
    r1 = register_job("old.wav", "old.wav")
    r2 = register_job("new.wav", "new.wav")
    write_audio_file("old.wav")
    write_audio_file("new.wav")

    task = make_task()
    with (
        patch("ws_api.tasks_transcribe.write_md_file"),
        patch("ws_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("ws_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("ws_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    with Session(engine) as session:
        j1 = session.get(Job, r1["id"])
        j2 = session.get(Job, r2["id"])
    assert j1.status == JobStatus.COMPLETED
    assert j2.status == JobStatus.PENDING
