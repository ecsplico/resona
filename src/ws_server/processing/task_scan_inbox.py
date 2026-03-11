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
log = logging.getLogger(__name__)

class ScanInboxTask(Thread):
    """Background task to scan an inbox folder for audio files and register jobs."""

    def __init__(self, shutdown_event):
        super().__init__(daemon=True)
        self.shutdown_event = shutdown_event

    def run(self, *args, **kwargs):
        log.info("ScanInboxTask started")
        while not self.shutdown_event.is_set():
            try:
                inboxfiles = []
                for ext in EXTENSIONS:
                    inboxfiles += glob(f"{INBOX_PATH}/*.{ext}")
                for file in inboxfiles:
                    name_original = os.path.basename(file)
                    extension = name_original.rsplit(".", 1)[-1]
                    name_new = f"{secrets.token_hex(10)}.{extension}"
                    filename = f"{FILE_PATH}/{name_new}"
                    log.info(f"Found: {name_original} --> {name_new}")
                    os.rename(file, filename)
                    with Session(engine) as session:
                        job = Job(filename=name_new, keepfile=True, translate=False)
                        session.add(job)
                        session.commit()
                        log.info(f"Added file {name_original} as job {job.id}")
            except Exception as e:
                log.error(f"ScanInboxTask error: {e}", exc_info=True)
            self.shutdown_event.wait(timeout=1.0)
        log.info("ScanInboxTask stopped")
