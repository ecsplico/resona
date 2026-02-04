import logging
from typing import Optional
from datetime import datetime
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
    upload_name: Optional[str] = Field(default='')  # Original filename from upload
    transcribed: bool = Field(default=False)
    processed: bool = Field(default=False)
    status: JobStatus = Field(default=JobStatus.PENDING)  # New status field
    error_message: Optional[str] = Field(default=None)  # Track errors
    language: Optional[str] = Field(default=None)
    segments: Optional[str] = Field(default='') # Consider JSON type if DB supports
    transcript: Optional[str] = Field(default='')
    csegments: Optional[str] = Field(default='') # Corrected segments?
    ctranscript: Optional[str] = Field(default='') # Corrected transcript?
    model: Optional[str] = Field(default='')
    translation: Optional[str] = Field(default='')
    pushurl: Optional[str] = Field(default='')
    pushtoken: Optional[str] = Field(default='')
    keepfile: bool = Field(default=False)
    md: Optional[str] = Field(default='')
    done: bool = Field(default=False) # Is this used? Consider removing if not.
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
    name: str = Field(index=True) # Index name for faster lookups
    replacement: str
    active: bool = Field(default=True)

class InitialPrompt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phrase: str = Field(index=True) # Index for potential lookups
    active: bool = Field(default=True)

