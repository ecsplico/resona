import logging
import os
import sys
from pathlib import Path
from sqlmodel import SQLModel, create_engine, select, Session

from .models import Job, Replacement, InitialPrompt
from .presets import replacements as default_replacements
from .presets import initial_prompts
from ..paths import DATABASE_URL, DB_PATH

log = logging.getLogger(__name__)


def validate_database_directory():
    """Validate that the database directory exists and is writable."""
    db_dir = Path(DB_PATH)

    if not db_dir.exists():
        try:
            log.info(f"Creating database directory: {db_dir}")
            db_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            error_msg = (
                f"\n{'='*60}\n"
                f"ERROR: Cannot create database directory\n"
                f"{'='*60}\n"
                f"Location: {db_dir}\n"
                f"Reason: Permission denied\n\n"
                f"Solution: Run one of the following commands:\n"
                f"  sudo mkdir -p {db_dir}\n"
                f"  sudo chown $USER:$USER {db_dir}\n"
                f"\nOr set a different DB_PATH in your .env file.\n"
                f"{'='*60}\n"
            )
            log.error(error_msg)
            print(error_msg, file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            log.error(f"Cannot create database directory {db_dir}: {e}")
            sys.exit(1)

    if not os.access(db_dir, os.W_OK):
        log.error(f"Database directory is not writable: {db_dir}")
        sys.exit(1)

    log.info(f"Database directory validated: {db_dir}")


validate_database_directory()

engine = create_engine(DATABASE_URL)


def create_db_and_tables():
    """Create all tables defined by SQLModel metadata."""
    log.info("Creating database tables...")
    SQLModel.metadata.create_all(engine)
    # Idempotent migration: add Job.engine to pre-existing databases.
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(job)"))]
        if "engine" not in cols:
            conn.execute(text("ALTER TABLE job ADD COLUMN engine VARCHAR"))
            conn.commit()
    log.info("Database tables created successfully.")


def populate_default_replacements():
    """Seed the Replacement table with defaults if empty."""
    log.info("Checking database for default replacements...")
    with Session(engine) as session:
        existing = session.exec(select(Replacement)).first()
        if existing is None:
            count = 0
            for name, replacement_text in default_replacements:
                if isinstance(replacement_text, str):
                    session.add(Replacement(name=name, replacement=replacement_text, active=True))
                    count += 1
            session.commit()
            log.info(f"Added {count} default replacements to the database.")
        else:
            log.info("Replacements table already populated.")


def populate_initial_prompts():
    """Seed the InitialPrompt table with defaults if empty."""
    log.info("Checking database for initial prompts...")
    with Session(engine) as session:
        existing = session.exec(select(InitialPrompt)).first()
        if existing is None:
            count = 0
            for prompt_text in initial_prompts:
                session.add(InitialPrompt(phrase=prompt_text, active=True))
                count += 1
            session.commit()
            log.info(f"Added {count} initial prompts to the database.")
        else:
            log.info("Initial prompts table already populated.")
