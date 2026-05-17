import json
import logging
import os
from threading import Thread
from datetime import datetime

from sqlmodel import Session, select

from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import resolve_profile, ProfileError

from . import engine_registry as reg
from .formatting import write_md_file
from .utils import update_job_attributes_from_result
from .db.models import Job, JobStatus
from .db.engine import engine
from .paths import FILE_PATH, PROFILES_PATH

log = logging.getLogger(__name__)


class TranscribeTask(Thread):
    """Background task: dequeue PENDING jobs and transcribe via the registry."""

    def __init__(self, shutdown_event):
        super().__init__(daemon=True)
        self.shutdown_event = shutdown_event

    def run(self, *args, **kwargs):
        log.info("TranscribeTask started")
        while not self.shutdown_event.is_set():
            try:
                self._process_next_job()
            except Exception as e:
                log.error(f"Unexpected error in TranscribeTask loop: {e}",
                          exc_info=True)
            self.shutdown_event.wait(timeout=1.0)
        log.info("TranscribeTask stopped")

    def _process_next_job(self):
        with Session(engine) as session:
            statement = (
                select(Job)
                .where(Job.status.in_([JobStatus.PENDING]))
                .order_by(Job.created_at.asc())
            )
            job = session.exec(statement).first()
            if job is None:
                return

            log.info(f"Starting transcription for job {job.id}")
            job.status = JobStatus.PROCESSING
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()

            try:
                filepath = FILE_PATH / job.filename
                if not os.path.exists(filepath):
                    raise FileNotFoundError(f"Audio file not found: {filepath}")

                try:
                    profile = resolve_profile(job.profile or "default", PROFILES_PATH)
                except ProfileError as e:
                    log.warning("Job %s: profile %r invalid (%s); using default",
                                job.id, job.profile, e)
                    profile = resolve_profile("default", PROFILES_PATH)

                job.profile_config = json.dumps(profile.to_dict(), ensure_ascii=False)

                info = reg.resolve(job.engine or None, "stt", private=False)
                asr_result = reg.run_stt(
                    info,
                    filepath,
                    language="de",
                    prompt=profile.initial_prompt_string(),
                    task="translate" if job.translate else "transcribe",
                )
                log.info(f"Job {job.id}: ASR completed via '{info.name}'")

                update_job_attributes_from_result(job, asr_result)

                result = build_pipeline(profile).run(job.transcript)
                job.md = result.text
                job.structured = (
                    json.dumps(result.data, ensure_ascii=False) if result.data else None
                )

                try:
                    write_md_file(job.id, job.filename, job.md, job.keepfile,
                                  structured=job.structured)
                    log.info(f"Job {job.id}: wrote MD file")
                except Exception as e_md:
                    log.error(f"Job {job.id}: failed to write MD file: {e_md}")

                job.status = JobStatus.COMPLETED
                job.processed = True
                job.error_message = None
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
                log.info(f"Job {job.id}: completed")

            except FileNotFoundError as e:
                log.error(f"Job {job.id}: file not found: {e}")
                job.status = JobStatus.FAILED
                job.error_message = f"File not found: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()

            except Exception as e:
                log.error(f"Job {job.id}: unexpected error: {e}", exc_info=True)
                job.status = JobStatus.FAILED
                job.error_message = f"Unexpected error: {str(e)}"
                job.updated_at = datetime.utcnow()
                session.add(job)
                session.commit()
