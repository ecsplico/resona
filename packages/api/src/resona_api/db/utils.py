import logging
from sqlmodel import Session, select

from .engine import engine
from .models import Job, Replacement, InitialPrompt, JobStatus

log = logging.getLogger(__name__)


def register_job(filename: str, upload_name: str, keep: bool = True, translate: bool = False) -> dict:
    """Register a new transcription job in the database."""
    log.info(f"Registering job: filename='{filename}', upload_name='{upload_name}', keep={keep}, translate={translate}")
    with Session(engine) as session:
        job = Job(
            filename=filename,
            upload_name=upload_name,
            keepfile=keep,
            translate=translate,
            status=JobStatus.PENDING
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        log.info(f"Job registered successfully with ID: {job.id}")
        return {
            "id": job.id,
            "file": f"/files/{job.filename}",
            "result": f"/job/{job.id}",
        }


def get_active_replacements() -> list[dict]:
    """Return active replacement rules as a list of dicts."""
    with Session(engine) as session:
        statement = select(Replacement).where(Replacement.active == True).order_by(Replacement.id)
        replacements = session.exec(statement).all()
        return [{"name": r.name, "replacement": r.replacement} for r in replacements]


def get_active_initial_prompts_string() -> str:
    """Return active initial prompt phrases as a comma-separated string."""
    prompt_string = ""
    try:
        with Session(engine) as session:
            statement = select(InitialPrompt).where(InitialPrompt.active == True)
            active_prompts = session.exec(statement).all()
            phrases = [prompt.phrase for prompt in active_prompts]
            prompt_string = ", ".join(phrases)
            log.info(f"Fetched {len(phrases)} active initial prompts.")
    except Exception as e:
        log.error(f"Error fetching initial prompts from database: {e}", exc_info=True)
    return prompt_string
