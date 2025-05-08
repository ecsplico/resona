import json
import logging
import os
import time
from threading import Thread
from decouple import config
from sqlmodel import Session, select

from .utils import run_asr, update_job_attributes_from_result # Import the new utility function
from .formatting import write_md_file
from .postprocessing import apply_postprocessing_steps # Import the new postprocessing function
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
                statement = select(Job).where(Job.processed == False).order_by(Job.id.asc()) # Corrected order_by
                job = session.exec(statement).first()
                if job is not None:
                    log.info(f"Starting transcription for job {job.id}")
                    try:
                        # Run ASR without internal markdown processing
                        asr_result = run_asr(f"{FILE_PATH}/{job.filename}")
                        
                        # Apply postprocessing steps
                        # For now, assume 'markdown' is always desired. This could be dynamic.
                        postprocessing_config = getattr(job, 'postprocessing_steps', ['markdown']) # Example: get from job or default
                        if not isinstance(postprocessing_config, list): # Basic validation
                            log.warning(f"Job {job.id}: postprocessing_steps is not a list, defaulting to ['markdown']. Value: {postprocessing_config}")
                            postprocessing_config = ['markdown']
                        
                        processed_result = apply_postprocessing_steps(asr_result, postprocessing_config)

                        # Update job attributes using the utility function
                        update_job_attributes_from_result(job, processed_result)
                        
                        filepath = f"{FILE_PATH}/{job.filename}" # Still need filepath for potential removal

                        if job.translate:
                            # Translation would also be a postprocessing step if implemented
                            # If translation modifies the result, ensure update_job_attributes_from_result
                            # is called *after* translation or can handle intermediate results.
                            pass

                        # File removal logic (can be part of post-transcription tasks)
                        if not job.keepfile and os.path.exists(filepath):
                            if job.filename: # Ensure filename is not already empty
                                os.remove(filepath)
                                job.filename='' # Clear filename in DB
                                log.info(f"Removed audio file for job {job.id}: {filepath}")
                            else:
                                log.warning(f"Job {job.id}: 'keepfile' is false, but filename is already empty. Skipping removal.")
                        
                        log.info(f"Job {job.id}: ASR, postprocessing, and attribute updates complete. About to set processed = True.")
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
