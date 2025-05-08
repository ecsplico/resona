import json
import logging
import os
import time
from threading import Thread
from decouple import config
from sqlmodel import Session, select

from .utils import run_asr
from .formatting import write_md_file
from core.db.models import Job
from core.db.engine import engine
from core.paths import FILE_PATH

log = logging.getLogger("uvicorn.test")

class TranscribeTask(Thread):
    # Get a job from the database and transcribe it
    # constructor
    def __init__(self, shutdown_event):
        # call the parent constructor
        super(TranscribeTask, self).__init__()
        self.shutdown_event = shutdown_event

    def run(self,*args,**kwargs):
        while True:
            # get Jobs from database and transcribe them
            with Session(engine) as session:
                statement = select(Job).where(Job.processed == False).order_by(Job.id=='asc')
                job = session.exec(statement).first()
                if job is not None:
                    log.info(f"Starting transcription for job {job.id}")
                    try:
                        result = run_asr(f"{FILE_PATH}/{job.filename}", markdown=True)
                        job.transcript = result["text"]
                        job.language = result["language"]
                        # Assuming result["segments"] is a list of objects with _asdict()
                        # Handle potential errors if segments format is different
                        try:
                            job.segments = json.dumps([segment._asdict() for segment in result.get("segments", [])])
                        except AttributeError:
                             log.warning(f"Could not serialize segments for job {job.id}. Segments: {result.get('segments')}")
                             job.segments = "[]" # Default to empty JSON array on error
                        job.transcribed = True
                        job.model = "???" # TODO: Save ASR_mode and model to database
                        filepath = f"{FILE_PATH}/{job.filename}"

                        if job.translate:
                            pass

                        if not job.keepfile and os.path.exists(filepath):
                            os.remove(filepath)
                            job.filename=''

                        # Ensure 'md' key exists before accessing
                        job.md = result.get("md", "")
 
                        log.info(f"Job {job.id}: All ASR processing complete. About to set processed = True.")
                        job.processed = True
                        session.commit()
                        log.info(f"Job {job.id}: Committed job state with processed = True.")
 
                        try:
                            write_md_file(job.id, job.filename, job.md, job.keepfile)
                            log.info(f"Job {job.id}: Successfully wrote MD file.")
                        except Exception as e_md:
                            log.error(f"Job {job.id}: FAILED to write MD file after job was marked processed. Error: {e_md}. Job details: {job}")
                        
                        # File removal was already inside a conditional, let's assume it's less critical for now
                        # or add similar try-except if it becomes a suspect

                        log.info(f"Job Finished {job.id}")
 
                    except RuntimeError as e:
                        log.error(f"Job {job.id}: Caught RuntimeError during ASR. Current job.processed state before commit: {job.processed if 'job' in locals() and hasattr(job, 'processed') else 'N/A'}")
                        # If we decide to mark as processed to prevent retries, we MUST also mark an error state.
                        # For now, let's see what the log says. The original code comments out job.processed = True here.
                        # job.processed = True # Example: if we wanted to stop retries
                        # job.status = "failed_asr" # Example: new field
                        session.commit() # This commit might be saving other changes to job if any were made before the error
                        log.error(f"Job Failed {job.id} due to RuntimeError. Error: {e}")
                        log.error(f"Job {job.id}: Committed session in RuntimeError block. Job processed state: {job.processed if 'job' in locals() and hasattr(job, 'processed') else 'N/A'}")
                    except Exception as e_general:
                        log.error(f"Job {job.id}: Caught UNEXPECTED Exception. Current job.processed state: {job.processed if 'job' in locals() and hasattr(job, 'processed') else 'N/A'}. Error: {e_general}")
                        # job.processed = True # Decide if we mark as processed
                        # job.status = "failed_unexpected" # Example
                        if 'session' in locals() and session.is_active:
                            session.commit() # Commit any state changes like an error message
                        log.error(f"Job Failed {job.id} due to unexpected error. Error: {e_general}")
                    log.info(f"Finished Job {job.id} processing attempt.")

            if self.shutdown_event.is_set():
                break
            time.sleep(1)
