import logging
from typing import Optional
from sqlmodel import Field, SQLModel, create_engine, Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from decouple import config
from .paths import DATABASE_URL
from .replacements import replacements
import logging

engine = create_engine(DATABASE_URL)

class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: Optional[str] = Field(default='')
    transcribed: bool = Field(default=False)
    processed: bool = Field(default=False)
    language: Optional[str] = Field(default=None)
    segments: Optional[str] = Field(default='')
    transcript: Optional[str] = Field(default='')
    csegments: Optional[str] = Field(default='')
    ctranscript: Optional[str] = Field(default='')
    model: Optional[str] = Field(default='')
    translation: Optional[str] = Field(default='')
    pushurl: Optional[str] = Field(default='')
    pushtoken: Optional[str] = Field(default='')
    keepfile: bool = Field(default=False)
    md: Optional[str] = Field(default='')
    done: bool = Field(default=False)
    translate: bool = Field(default=False)

class Replacement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    replacement: str
    active: bool = Field(default=True)

SQLModel.metadata.create_all(engine)

# If Replacements are empty add some
with Session(engine) as session:
    rows = session.query(Replacement).count()
    logger.info(f"Found {rows} replacements in database")
    if rows == 0:
        logger.info(f"Adding replacements to database")
        for name, replacement in replacements:
            session.add(Replacement(name=name, replacement=replacement))
        session.commit()
