import json
import logging
import os
import time
from threading import Thread
from datetime import datetime
from decouple import config
from sqlmodel import Session, select

from .utils import run_asr, update_job_attributes_from_result
from .formatting import write_md_file
from .postprocessing import apply_postprocessing_steps
from core.db.models import Job, JobStatus
from core.db.engine import engine
from core.paths import FILE_PATH

log = logging.getLogger(__name__)


class TranscribeTask(Thread):
    """Background task to process pending transcription jobs."""
    
    def __init__(self, shutdown_event):
        super(TranscribeTask, self).__init__(daemon=True)
        self.shutdown_event = shutdown_event

    def run(self, *args, **kwargs):
        log.info("TranscribeTask started")
        while not self.shutdown_event.is_set():
            try:
                self._process_next_job()
            except Exception as e:
                log.error(f"Unexpected error in TranscribeTask main loop: {e}", exc_info=True)

            # Use wait() instead of sleep() so shutdown is immediate
            self.shutdown_event.wait(timeout=1.0)

        log.info("TranscribeTask stopped")

    def _process_next_job(self):
        """Process a single job from the queue."""
        with Session(engine) as session:
            # Query for pending or failed jobs (to allow retries)
            statement = (
                select(Job)
                .where(Job.status.in_([JobStatus.PENDING]))
                .order_by(Job.created_at.asc())
            )
            job = session.exec(statement).first()
            
            if job is None:
                return
            
            log.info(f"Starting transcription for job {job.id} (status: {job.status})")
            
            # Mark job as processing
            job.status = JobStatus.PROCESSING
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()
            
            try:
                # Run ASR
                filepath = f"{FILE_PATH}/{job.filename}"
                if not os.path.exists(filepath):
                    raise FileNotFoundError(f"Audio file not found: {filepath}")
                
                asr_result = run_asr(filepath)
                log.info(f"Job {job.id}: ASR completed")
                
                # Apply postprocessing steps
                postprocessing_config = getattr(job, 'postprocessing_steps', ['markdown'])
                if not isinstance(postprocessing_config, list):
                    log.warning(f"Job {job.id}: postprocessing_steps is not a list, defaulting to ['markdown']")
                    postprocessing_config = ['markdown']
                
                processed_result = apply_postprocessing_steps(asr_result, postprocessing_config)
                
                # Update job attributes
                update_job_attributes_from_result(job, processed_result)
                
                # Handle translation if requested
                if job.translate:
                    # Translation logic would go here
                    pass
                
                # Write markdown file
                try:
                    write_md_file(job.id, job.filename, job.md, job.keepfile)
                    log.info(f"Job {job.id}: Successfully wrote MD file")
                except Exception as e_md:
                    log.error(f"Job {job.id}: Failed to write MD file: {e_md}")
                
                # File removal logic
                if not job.keepfile and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        job.filename = ''
                        log.info(f"Job {job.id}: Removed audio file: {filepath}")
                    except Exception as e_rm:
                        log.error(f"Job {job.id}: Failed to remove file: {e_rm}")
                
                # Mark job as completed
                job.status = JobStatus.COMPLETED
                job.processed = True
                job.error_message = None  # Clear any previous errors
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
                
            except RuntimeError as e:
                log.error(f"Job {job.id}: ASR runtime error: {e}")
                job.status = JobStatus.FAILED
                job.error_message = f"ASR error: {str(e)}"
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

