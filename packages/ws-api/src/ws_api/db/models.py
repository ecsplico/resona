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
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    replacement: str
    active: bool = Field(default=True)


class InitialPrompt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phrase: str = Field(index=True)
    active: bool = Field(default=True)
