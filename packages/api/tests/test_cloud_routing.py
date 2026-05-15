"""Tests for cloud-engine routing in resona_api.tasks_transcribe."""
import os
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from sqlmodel import Session

from resona_api.db.engine import engine
from resona_api.db.models import Job, JobStatus
from resona_api.db.utils import register_job
from resona_api.engine_client import EngineClient
from resona_api.tasks_transcribe import TranscribeTask

# Mirror the helpers from test_tasks (can't cross-import with --import-mode=importlib)
FILE_PATH = Path(os.environ.get("FILE_PATH", "/tmp"))


def write_audio_file(job_filename: str) -> Path:
    p = FILE_PATH / job_filename
    p.write_bytes(b"RIFF")
    return p


def make_task(engine_client=None) -> TranscribeTask:
    if engine_client is None:
        engine_client = MagicMock(spec=EngineClient)
    return TranscribeTask(shutdown_event=Event(), engine_client=engine_client)


def test_routes_to_cloud_when_cloud_engine_env_set(monkeypatch):
    result = register_job("cloud1.wav", "cloud1.wav")
    job_id = result["id"]
    write_audio_file("cloud1.wav")

    cloud_result = {"text": "cloud transcript", "language": "de", "segments": []}
    mock_provider = MagicMock()
    mock_provider.transcribe.return_value = cloud_result

    engine_client = MagicMock(spec=EngineClient)  # must NOT be used
    task = make_task(engine_client=engine_client)

    with (
        patch(
            "resona_api.tasks_transcribe.config",
            side_effect=lambda key, default=None: {
                "RESONA_CLOUD_ENGINE": "deepgram",
                "RESONA_CLOUD_MODEL": "nova-3",
                "RESONA_CLOUD_OPTIONS": "",
                "DEEPGRAM_API_KEY": "k",
            }.get(key, default),
        ),
        patch("resona_api.tasks_transcribe.get_cloud_provider", return_value=mock_provider),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    engine_client.transcribe.assert_not_called()
    mock_provider.transcribe.assert_called_once()
    _, kwargs = mock_provider.transcribe.call_args
    assert kwargs["api_key"] == "k"
    assert kwargs["model"] == "nova-3"

    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED
    assert job.transcript == "cloud transcript"


def test_default_path_uses_engine_client_when_cloud_engine_unset(monkeypatch):
    result = register_job("local1.wav", "local1.wav")
    job_id = result["id"]
    write_audio_file("local1.wav")

    engine_client = MagicMock(spec=EngineClient)
    engine_client.transcribe.return_value = {"text": "via engine", "language": "de", "segments": []}
    task = make_task(engine_client=engine_client)

    with (
        patch(
            "resona_api.tasks_transcribe.config",
            side_effect=lambda key, default=None: default,
        ),
        patch("resona_api.tasks_transcribe.write_md_file"),
        patch("resona_api.tasks_transcribe.update_job_attributes_from_result"),
        patch("resona_api.tasks_transcribe.get_active_replacements", return_value=[]),
        patch("resona_api.tasks_transcribe.get_active_initial_prompts_string", return_value=""),
    ):
        task._process_next_job()

    engine_client.transcribe.assert_called_once()
    with Session(engine) as session:
        job = session.get(Job, job_id)
    assert job.status == JobStatus.COMPLETED
