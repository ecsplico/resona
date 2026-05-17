import logging
import os
import re
import secrets
from typing import List, Optional
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, Query, UploadFile, HTTPException, status, Body, Form, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from pydantic import BaseModel

from .db.models import Job, JobStatus, Replacement, InitialPrompt
from .db.engine import engine
from .db.utils import register_job
from .paths import FILE_PATH
from .auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter()


def get_db_session():
    with Session(engine) as session:
        yield session


def sanitize_filename(filename: str) -> str:
    filename = os.path.basename(filename)
    if '\\' in filename:
        filename = filename.split('\\')[-1]
    filename = re.sub(r'[^\w\-.]', '_', filename)
    if not filename or filename in ['.', '..'] or filename.startswith('.'):
        filename = 'unnamed_file'
    return filename


def validate_audio_file(file: UploadFile) -> None:
    allowed_types = ['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/ogg', 'audio/webm']
    allowed_extensions = ['.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.webm']

    if file.content_type not in allowed_types:
        if file.filename:
            ext = Path(file.filename).suffix.lower()
            if ext not in allowed_extensions:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}"
                )


# ── Job endpoints ─────────────────────────────────────────────────────

@router.post("/jobs", summary="Submit audio files for async transcription", tags=["Job"])
async def submit_jobs(
    audio_files: List[UploadFile] = File(...),
    keep: bool = Form(True),
    translate: bool = Form(False),
    engine: Optional[str] = Form(default=None),
    api_key: str = Depends(verify_api_key)
):
    """Upload one or more audio files and register them for async transcription."""
    jobs = []
    for audio_file in audio_files:
        validate_audio_file(audio_file)

        name_original = audio_file.filename or "unnamed"
        safe_name = sanitize_filename(name_original)
        extension = Path(safe_name).suffix or '.bin'
        name_new = f"{secrets.token_hex(10)}{extension}"

        file_path = Path(FILE_PATH) / name_new
        async with aiofiles.open(file_path, "wb") as buffer:
            content = await audio_file.read()
            await buffer.write(content)

        job = register_job(filename=name_new, upload_name=name_original, keep=keep, translate=translate, engine=engine)
        jobs.append(job)

    return jobs


@router.post("/jobs/registerfile", summary="Register an existing file for (re)processing", tags=["Job"])
async def register_file(
    filename: str = Body(...),
    api_key: str = Depends(verify_api_key)
):
    """Register an already-stored file for async transcription."""
    file_path = Path(FILE_PATH) / filename
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{filename}' not found"
        )

    job = register_job(filename=filename, upload_name=filename, keep=True, translate=False)
    return job


@router.get("/job/{job_id}", summary="Get job result", tags=["Job"])
def get_job(
    job_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Get the current status and result of a transcription job."""
    statement = select(Job).where(Job.id == job_id)
    job = session.exec(statement).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found"
        )
    return job


@router.get("/jobs/", summary="List all jobs", tags=["Job"])
def list_jobs(
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """List all transcription jobs."""
    return session.exec(select(Job)).all()


# ── Replacement CRUD ──────────────────────────────────────────────────

class ReplacementCreate(BaseModel):
    """Request body for creating a replacement rule."""

    name: str
    replacement: str


@router.get("/replacements/", summary="List replacements", tags=["Config"])
def list_replacements(
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """List all text replacement rules."""
    return session.exec(select(Replacement)).all()


@router.post("/replacements/", summary="Add replacement", tags=["Config"])
def add_replacement(
    body: ReplacementCreate,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Create a new text replacement rule. Returns 409 if the pattern already exists."""
    existing = session.exec(select(Replacement).where(Replacement.name == body.name)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Pattern '{body.name}' already exists")
    r = Replacement(name=body.name, replacement=body.replacement, active=True)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


@router.delete("/replacements/{replacement_id}", summary="Delete replacement", tags=["Config"])
def delete_replacement(
    replacement_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Delete a replacement rule by ID."""
    r = session.get(Replacement, replacement_id)
    if not r:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Replacement not found")
    session.delete(r)
    session.commit()
    return {"ok": True}


# ── Prompt CRUD ───────────────────────────────────────────────────────

class PromptCreate(BaseModel):
    """Request body for adding an initial prompt phrase."""

    phrase: str


@router.get("/prompts/", summary="List prompts", tags=["Config"])
def list_prompts(
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """List all initial prompt phrases, ordered by ID."""
    return session.exec(select(InitialPrompt).order_by(InitialPrompt.id)).all()


@router.post("/prompts/", summary="Add prompt", tags=["Config"])
def add_prompt(
    body: PromptCreate,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Add a new initial prompt phrase. Returns 409 if the phrase already exists."""
    existing = session.exec(select(InitialPrompt).where(InitialPrompt.phrase == body.phrase)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Prompt already exists")
    p = InitialPrompt(phrase=body.phrase, active=True)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.put("/prompts/{prompt_id}/activate", summary="Activate prompt", tags=["Config"])
def activate_prompt(
    prompt_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Activate a prompt phrase, deactivating all others."""
    all_prompts = session.exec(select(InitialPrompt)).all()
    for p in all_prompts:
        p.active = False
        session.add(p)

    target = session.get(InitialPrompt, prompt_id)
    if not target:
        session.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")

    target.active = True
    session.add(target)
    session.commit()
    return {"ok": True}


@router.put("/prompts/{prompt_id}/deactivate", summary="Deactivate prompt", tags=["Config"])
def deactivate_prompt(
    prompt_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Deactivate a prompt phrase without activating another."""
    p = session.get(InitialPrompt, prompt_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    p.active = False
    session.add(p)
    session.commit()
    return {"ok": True}


@router.delete("/prompts/{prompt_id}", summary="Delete prompt", tags=["Config"])
def delete_prompt(
    prompt_id: int,
    session: Session = Depends(get_db_session),
    api_key: str = Depends(verify_api_key)
):
    """Delete a prompt phrase by ID."""
    p = session.get(InitialPrompt, prompt_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt not found")
    session.delete(p)
    session.commit()
    return {"ok": True}
