import logging
import os
import sys
from pathlib import Path
from sqlalchemy import event
from sqlmodel import create_engine

from .schema import migrate
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

engine = create_engine(DATABASE_URL, connect_args={"timeout": 5})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_db_and_tables():
    """Bring the database schema to the latest version (see ``db/schema.py``)."""
    log.info("Migrating database schema...")
    migrate(engine)
    log.info("Database schema is current.")
