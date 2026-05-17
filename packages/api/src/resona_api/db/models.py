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
        md: Transcript with postprocessing applied (Markdown-ready).
        language: Detected or requested language code.
        segments: JSON-serialised segment list from the engine.
        keepfile: Whether the audio file is kept after transcription (always ``True``).
        translate: Whether English translation was requested.
        engine: Name of the engine that should process this job (None = gateway default).
        profile: Name of the postprocessing profile to apply (None = default profile).
        profile_config: JSON-serialised profile configuration override (None = use stored profile).
        structured: JSON-serialised structured output produced by the postprocessing pipeline.
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
    engine: Optional[str] = Field(default=None)
    profile: Optional[str] = Field(default=None)
    profile_config: Optional[str] = Field(default=None)
    structured: Optional[str] = Field(default=None)
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
