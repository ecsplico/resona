"""Shared FastAPI dependencies and request validators."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlmodel import Session

from .db.engine import engine

_AUDIO_TYPES = frozenset({"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/ogg", "audio/webm"})
_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".webm")


def get_db_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def validate_audio_file(file: UploadFile) -> None:
    """Reject uploads whose declared type and extension are both not audio (415)."""
    if file.content_type in _AUDIO_TYPES:
        return
    if file.filename and Path(file.filename).suffix.lower() not in _AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type. Allowed: {', '.join(_AUDIO_EXTENSIONS)}",
        )
