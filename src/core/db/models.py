import logging
from typing import Optional
from sqlmodel import Field, SQLModel # Keep Session if needed for type hints, remove create_engine

logger = logging.getLogger(__name__)

class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: Optional[str] = Field(default='')
    transcribed: bool = Field(default=False)
    processed: bool = Field(default=False)
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

class Replacement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True) # Index name for faster lookups
    replacement: str
    active: bool = Field(default=True)

class InitialPrompt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    phrase: str = Field(index=True) # Index for potential lookups
    active: bool = Field(default=True)

