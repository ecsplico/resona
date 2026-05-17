"""Tests for resona_api.tasks_transcribe.TranscribeTask._process_next_job.

Key design after the profile refactor:
- Engine is called with an initial_prompt derived from the profile
- Postprocessing is done via the profile pipeline (build_pipeline)
- job.transcript holds the raw engine text
- job.md holds the postprocessed text
- job.structured holds JSON-encoded structured data (extract steps)
- job.profile_config holds the resolved profile snapshot as JSON
- engine_registry.run_stt() is called WITHOUT replacements parameter
"""
import json
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
        patch("resona_api.tasks_transcribe.build_pipeline") as mock_bp,
    ):
        mock_result = MagicMock()
        mock_result.text = "raw text"
        mock_result.data = {}
        mock_bp.return_value.run.return_value = mock_result
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED


def test_missing_audio_file_marks_failed():
    result = register_job("missing.wav", "missing.wav")
    job_id = result["id"]
    # Don't create the file

    task = make_task()
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
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.FAILED
    assert "engine exploded" in (job.error_message or "")


def test_run_stt_called_without_replacements():
    """run_stt must NOT receive replacements — they are applied locally via pipeline."""
    result = register_job("audio3.wav", "audio3.wav",
                          profile=json.dumps({
                              "name": "test",
                              "initial_prompt": ["Befund"],
                              "steps": [],
                          }))
    write_audio_file("audio3.wav")

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=ASR_RESULT) as mock_run_stt,
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.build_pipeline") as mock_bp,
    ):
        mock_result = MagicMock()
        mock_result.text = "raw text"
        mock_result.data = {}
        mock_bp.return_value.run.return_value = mock_result
        task._process_next_job()

    mock_run_stt.assert_called_once()
    _, kwargs = mock_run_stt.call_args
    # Replacements must NOT be passed to the engine
    assert "replacements" not in kwargs
    # The profile provides "Befund" as the initial prompt
    assert kwargs["prompt"] == "Befund"


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
        patch("resona_api.tasks_transcribe.build_pipeline") as mock_bp,
    ):
        mock_result = MagicMock()
        mock_result.text = "raw text"
        mock_result.data = {}
        mock_bp.return_value.run.return_value = mock_result
        task._process_next_job()

    with Session(engine) as session:
        j1 = session.get(Job, r1["id"])
        j2 = session.get(Job, r2["id"])
    assert j1.status == JobStatus.COMPLETED
    assert j2.status == JobStatus.PENDING


def test_profile_pipeline_replacements_and_extract():
    """Profile pipeline: replacements applied to job.md, extract stored in
    job.structured, and profile_config snapshot persisted."""
    inline = json.dumps({
        "name": "t",
        "initial_prompt": ["Befund"],
        "steps": [
            {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
            {"type": "extract", "name": "f", "prompt": "extract"},
        ],
    })

    result = register_job("audio_profile.wav", "audio_profile.wav", profile=inline)
    job_id = result["id"]
    write_audio_file("audio_profile.wav")

    raw_result = {"text": "Hallo Komma Welt", "language": "de", "segments": []}

    # llm_extract is called internally by build_pipeline when the extract step runs;
    # patch it at the source module so the pipeline picks it up.
    extract_return = {"f": "some value"}

    task = make_task()
    with (
        patch.object(reg, "resolve", return_value=_FAKE_INFO),
        patch.object(reg, "run_stt", return_value=raw_result),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_postprocess.pipeline.llm_extract", return_value=extract_return) as mock_extract,
    ):
        task._process_next_job()

    with Session(engine) as session:
        job = session.get(Job, job_id)

    assert job.status == JobStatus.COMPLETED
    # replacements step: "Komma" → ","
    assert "," in job.md
    assert "Komma" not in job.md
    # extract step: structured data populated
    assert job.structured is not None
    structured = json.loads(job.structured)
    assert "f" in structured
    # profile_config snapshot persisted
    assert job.profile_config is not None
    pc = json.loads(job.profile_config)
    assert pc["name"] == "t"
