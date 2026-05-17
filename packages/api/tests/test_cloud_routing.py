"""The async job task routes through the engine registry."""
from unittest.mock import patch

from resona_api import engine_registry as reg
from resona_api.tasks_transcribe import TranscribeTask
from resona_api.db.utils import register_job
from resona_api.db.models import Job, JobStatus
from resona_api.db.engine import engine as db_engine
from resona_api.paths import FILE_PATH
from sqlmodel import Session, select
from threading import Event


def test_job_routes_through_registry(wav_bytes):
    (FILE_PATH / "routed.wav").write_bytes(wav_bytes)
    job = register_job(filename="routed.wav", upload_name="routed.wav",
                       keep=True, translate=False, engine="deepgram")

    info = reg.EngineInfo(
        name="deepgram",
        kind="cloud",
        capabilities=["stt", "tts"],
        private=False,
        available=True,
        models=[],
        provider="deepgram",
    )
    with patch.object(reg, "resolve", return_value=info) as resolve, \
         patch.object(reg, "run_stt",
                      return_value={"text": "ok", "language": "de",
                                    "segments": []}):
        task = TranscribeTask(Event())
        task._process_next_job()

    resolve.assert_called_once()
    assert resolve.call_args[0][0] == "deepgram"
    with Session(db_engine) as session:
        stored = session.exec(select(Job).where(Job.id == job["id"])).first()
    assert stored.status == JobStatus.COMPLETED
