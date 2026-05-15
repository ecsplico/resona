import json as _json
import logging
import os
import time
from threading import Thread
from datetime import datetime
from pathlib import Path as _Path
from decouple import config
from sqlmodel import Session, select

from resona_postprocess.pipeline import PostprocessPipeline
from resona_postprocess.replacements import apply_replacements

from .engine_client import EngineClient
from .formatting import write_md_file
from .utils import update_job_attributes_from_result
from .db.models import Job, JobStatus
from .db.engine import engine
from .db.utils import get_active_replacements, get_active_initial_prompts_string
from .paths import FILE_PATH

log = logging.getLogger(__name__)


def get_cloud_provider(name: str):
    """Return the resona-cloud-stt provider module for ``name``.

    Wrapper so tests can patch this symbol without importing the package.
    """
    from resona_cloud_stt.registry import get_provider
    return get_provider(name)


def _cloud_transcribe(filepath: _Path) -> dict:
    """Transcribe via a cloud provider selected by RESONA_CLOUD_* env vars."""
    from resona_cloud_stt.errors import MissingAPIKeyError
    from resona_cloud_stt.registry import PROVIDER_ENV_KEYS

    provider_name = config("RESONA_CLOUD_ENGINE")
    env_var = PROVIDER_ENV_KEYS.get(provider_name)
    api_key = config(env_var, default="") if env_var else ""
    if not api_key:
        raise MissingAPIKeyError(env_var or provider_name)

    model = config("RESONA_CLOUD_MODEL", default=None)
    options_raw = config("RESONA_CLOUD_OPTIONS", default="")
    options = _json.loads(options_raw) if options_raw else None

    provider = get_cloud_provider(provider_name)
    return provider.transcribe(
        _Path(filepath),
        api_key=api_key,
        model=model,
        language="de",
        options=options,
    )


class TranscribeTask(Thread):
    """Background task to process pending transcription jobs."""

    def __init__(self, shutdown_event, engine_client: EngineClient):
        super(TranscribeTask, self).__init__(daemon=True)
        self.shutdown_event = shutdown_event
        self.engine_client = engine_client

    def run(self, *args, **kwargs):
        log.info("TranscribeTask started")
        while not self.shutdown_event.is_set():
            try:
                self._process_next_job()
            except Exception as e:
                log.error(f"Unexpected error in TranscribeTask main loop: {e}", exc_info=True)

            self.shutdown_event.wait(timeout=1.0)

        log.info("TranscribeTask stopped")

    def _process_next_job(self):
        """Process a single job from the queue."""
        with Session(engine) as session:
            statement = (
                select(Job)
                .where(Job.status.in_([JobStatus.PENDING]))
                .order_by(Job.created_at.asc())
            )
            job = session.exec(statement).first()

            if job is None:
                return

            log.info(f"Starting transcription for job {job.id} (status: {job.status})")

            job.status = JobStatus.PROCESSING
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()

            try:
                filepath = FILE_PATH / job.filename
                if not os.path.exists(filepath):
                    raise FileNotFoundError(f"Audio file not found: {filepath}")

                # Fetch initial prompt from DB; pass to engine
                initial_prompt = get_active_initial_prompts_string()

                if config("RESONA_CLOUD_ENGINE", default=""):
                    asr_result = _cloud_transcribe(filepath)
                else:
                    asr_result = self.engine_client.transcribe(
                        filepath=filepath,
                        language="de",
                        initial_prompt=initial_prompt,
                        task="translate" if job.translate else "transcribe",
                    )
                log.info(f"Job {job.id}: ASR completed")

                update_job_attributes_from_result(job, asr_result)

                # Build postprocess pipeline from DB replacements
                replacements = get_active_replacements()
                pipeline = PostprocessPipeline()
                if replacements:
                    pipeline.add("replacements", lambda t, r=replacements: apply_replacements(t, r))
                job.md = pipeline.run(job.transcript)

                # Write markdown file
                try:
                    write_md_file(job.id, job.filename, job.md, job.keepfile)
                    log.info(f"Job {job.id}: Successfully wrote MD file")
                except Exception as e_md:
                    log.error(f"Job {job.id}: Failed to write MD file: {e_md}")

                # Audio files are always kept (keepfile=True by default)
                # File deletion logic removed per architecture change

                job.status = JobStatus.COMPLETED
                job.processed = True
                job.error_message = None
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

                log.info(f"Job {job.id}: Successfully completed")

            except FileNotFoundError as e:
                log.error(f"Job {job.id}: File not found error: {e}")
                job.status = JobStatus.FAILED
                job.error_message = f"File not found: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

            except Exception as e:
                log.error(f"Job {job.id}: Unexpected error: {e}", exc_info=True)
                job.status = JobStatus.FAILED
                job.error_message = f"Unexpected error: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
