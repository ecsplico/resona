import json
import logging
import os
import time
from threading import Thread
from glob import glob
from datetime import datetime
from decouple import config
from sqlmodel import Session, select
import secrets

from .utils import run_asr
from .formatting import write_md_file
from core.db.models import Job
from core.db.engine import engine
from core.paths import DATA_PATH, MD_PATH, INBOX_PATH, FILE_PATH

EXTENSIONS = ["wav", "webm", "flac", "mp3"]
log = logging.getLogger("uvicorn.test")

class ScanInboxTask(Thread):
    # Look for audio files in the transcription-inbox folder and register the jobs in the database
    # constructor
    def __init__(self, shutdown_event):
        # call the parent constructor
        super(ScanInboxTask, self).__init__()
        self.shutdown_event = shutdown_event

    def run(self,*args,**kwargs):
        while True:
            inboxfiles = []
            for ext in EXTENSIONS:
                inboxfiles += glob(f"./{INBOX_PATH}/*.{ext}")
            for file in inboxfiles:
                name_original = os.path.basename(file)
                extension = name_original.split(".")[1]
                name_new = f"{secrets.token_hex(10)}.{extension}"
                filename = f"{FILE_PATH}/{name_new}"
                log.info(f"Found: {name_original} --> {name_new}")
                os.rename(file, filename)
                # register to database
                with Session(engine) as session:
                    job = Job(filename=name_new, keepfile=True, translate=False)
                    session.add(job)
                    session.commit()
                    log.info(f"Added file {name_original} as job {job.id}")
            if self.shutdown_event.is_set():
                break
            time.sleep(9)

# Removed unused/incomplete segment_serializer function

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

                        job.processed = True
                        session.commit()

                        write_md_file(job.id, job.filename, job.md, job.keepfile)
                        log.info(f"Job Finished {job.id}")

                    except RuntimeError as e:
                        job.processed = True
                        session.commit()
                        log.error(f"Job Failed {job.id}")
                        log.error(e)
                    log.info(f"Finished Job {job.id}")

            if self.shutdown_event.is_set():
                break
            time.sleep(5)
