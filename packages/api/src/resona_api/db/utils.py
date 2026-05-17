import logging
from sqlmodel import Session, select

from .engine import engine as _engine
from .models import Job, JobStatus

log = logging.getLogger(__name__)


def register_job(filename: str, upload_name: str, keep: bool = True,
                 translate: bool = False, engine: str | None = None,
                 profile: str | None = None) -> dict:
    """Register a new transcription job in the database."""
    log.info(f"Registering job: filename='{filename}', upload_name='{upload_name}', keep={keep}, translate={translate}")
    with Session(_engine) as session:
        job = Job(
            filename=filename, upload_name=upload_name, keepfile=keep,
            translate=translate, engine=engine, profile=profile,
            status=JobStatus.PENDING,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        log.info(f"Job registered successfully with ID: {job.id}")
        return {"id": job.id, "file": f"/files/{job.filename}",
                "result": f"/job/{job.id}"}
