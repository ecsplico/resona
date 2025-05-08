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
            # log.info(f"Scanning inbox ({INBOX_PATH})")
            for ext in EXTENSIONS:
                inboxfiles += glob(f"{INBOX_PATH}/*.{ext}")
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
            time.sleep(1)
