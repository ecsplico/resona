import logging
from datetime import datetime
from typing import Optional
from enum import Enum
from sqlmodel import Field, SQLModel, Column
from sqlalchemy import DateTime

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Status of a transcription job."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(SQLModel, table=True):
    """SQLModel table for a transcription job.

    Created when a file is submitted and updated as it moves through the
    ``PENDING → PROCESSING → COMPLETED | FAILED`` lifecycle.

    Attributes:
        id: Auto-assigned primary key.
        filename: Storage filename (random hex + original extension).
        upload_name: Original filename from the client upload.
        status: Current :class:`JobStatus`.
        transcript: Raw transcript text from the engine (no replacements).
        md: Transcript with active replacements applied (Markdown-ready).
        language: Detected or requested language code.
        segments: JSON-serialised segment list from the engine.
        keepfile: Whether the audio file is kept after transcription (always ``True``).
        translate: Whether English translation was requested.
        error_message: Human-readable error description on failure.
        created_at: UTC timestamp when the job was created.
        updated_at: UTC timestamp of the last status change.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: Optional[str] = Field(default='')
    upload_name: Optional[str] = Field(default='')
    transcribed: bool = Field(default=False)
    processed: bool = Field(default=False)
    status: JobStatus = Field(default=JobStatus.PENDING)
    error_message: Optional[str] = Field(default=None)
    language: Optional[str] = Field(default=None)
    segments: Optional[str] = Field(default='')
    transcript: Optional[str] = Field(default='')
    model: Optional[str] = Field(default='')
    translation: Optional[str] = Field(default='')
    keepfile: bool = Field(default=True)  # Default True — audio files are kept
    md: Optional[str] = Field(default='')
    translate: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=False, onupdate=datetime.utcnow)
    )


class Replacement(SQLModel, table=True):
    """Regex-based text replacement rule applied post-transcription.

    Active replacements are fetched by resona-api on each job and applied
    locally via the postprocess pipeline. The raw transcript is stored as
    ``transcript`` and the processed result is stored as ``md``.

    Attributes:
        id: Auto-assigned primary key.
        name: Regex pattern (matched case-insensitively against the transcript).
        replacement: Substitution text.
        active: Whether this rule is currently applied.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    replacement: str
    active: bool = Field(default=True)


class InitialPrompt(SQLModel, table=True):
    """A Whisper initial prompt phrase stored in the database.

    The single active phrase is concatenated and passed to Whisper as
    ``initial_prompt``, biasing recognition towards domain vocabulary.
    Only one prompt can be active at a time; activating one deactivates all others.

    Attributes:
        id: Auto-assigned primary key.
        phrase: Vocabulary hint text (e.g. ``"Befund, Diagnose, Therapie"``).
        active: Whether this phrase is currently used.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    phrase: str = Field(index=True)
    active: bool = Field(default=True)
